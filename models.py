from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.mysql import DATETIME

Timestamp = DateTime().with_variant(DATETIME(fsp=6), 'mysql')


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    # Store UTC without tz info; MySQL DATETIME has no timezone semantics.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=now)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=now, onupdate=now)


class Video(Record, Base):
    __tablename__ = 'videos'
    title: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default='uploaded')
    error: Mapped[str | None] = mapped_column(Text)


class VideoMetadata(Record, Base):
    __tablename__ = 'video_metadata'
    video_id: Mapped[str] = mapped_column(String(36), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    format: Mapped[str] = mapped_column(String(100))
    duration: Mapped[float] = mapped_column(Float)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default='uploaded')
    error: Mapped[str | None] = mapped_column(Text)
    status_changed_at: Mapped[datetime | None] = mapped_column(Timestamp)


class Job(Record, Base):
    __tablename__ = 'processing_jobs'
    video_id: Mapped[str] = mapped_column(String(36), index=True)
    # Set while active, clear on terminal state. MySQL permits multiple NULLs.
    active_video_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    resolutions: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default='queued')
    transcode_status: Mapped[str] = mapped_column(String(24), default='queued')
    thumbnail_status: Mapped[str] = mapped_column(String(24), default='queued')
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class WorkItem(Record, Base):
    __tablename__ = 'work_items'
    job_id: Mapped[str] = mapped_column(String(36), unique=True)
    video_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(Timestamp)
    next_attempt_at: Mapped[datetime | None] = mapped_column(Timestamp)
    error: Mapped[str | None] = mapped_column(Text)


class Attempt(Record, Base):
    __tablename__ = 'processing_attempts'
    __table_args__ = (UniqueConstraint('job_id', 'number'),)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey('work_items.job_id'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(Timestamp)


class Output(Record, Base):
    __tablename__ = 'transcoding_outputs'
    __table_args__ = (UniqueConstraint('job_id', 'resolution'),)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey('work_items.job_id'), index=True)
    video_id: Mapped[str] = mapped_column(String(36), index=True)
    resolution: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default='completed')
    error: Mapped[str | None] = mapped_column(Text)


class Thumbnail(Record, Base):
    __tablename__ = 'video_thumbnails'
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey('work_items.job_id'), unique=True)
    video_id: Mapped[str] = mapped_column(String(36), index=True)
    path: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(24), default='completed')
    error: Mapped[str | None] = mapped_column(Text)


class Outbox(Record, Base):
    __tablename__ = 'outbox'
    topic: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(Timestamp, index=True)


class Inbox(Record, Base):
    __tablename__ = 'inbox'
    event_id: Mapped[str] = mapped_column(String(36), unique=True)


OWNERS = {
    'upload': [Video, Outbox],
    'metadata': [VideoMetadata, Inbox],
    'orchestrator': [Job, Outbox, Inbox],
    'worker': [WorkItem, Attempt, Output, Outbox],
    'thumbnail': [WorkItem, Attempt, Thumbnail, Outbox],
    'delivery': [], 'gateway': [],
}


def serialize(record) -> dict:
    return {c.name: getattr(record, c.name) for c in record.__table__.columns}
