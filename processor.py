import logging
import time
from datetime import timedelta
from pathlib import Path
from fastapi import HTTPException
import redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from common.config import settings
from common.db import emit
from common.media import run_ffmpeg
from common.models import Attempt, Output, Thumbnail, WorkItem, now, serialize
from common.security import media_path

log = logging.getLogger(__name__)


class Busy(Exception):
    pass


class Processor:
    """SQL lease is authoritative. Redis progress is a best-effort UI cache only."""
    def __init__(self, db, kind):
        self.db = db
        self.kind = kind
        self.service = 'worker' if kind == 'transcode' else 'thumbnail'
        self.cache = redis.Redis.from_url(settings().redis_url, socket_connect_timeout=.2, socket_timeout=.2)

    def progress(self, job_id, value):
        try:
            self.cache.setex(f'progress:{job_id}:{self.kind}', 3600, value)
        except redis.RedisError:
            pass

    def claim(self, data):
        try:
            with self.db.transaction() as session:
                if not session.scalar(select(WorkItem).where(WorkItem.job_id == data['job_id'])):
                    session.add(WorkItem(job_id=data['job_id'], video_id=data['video_id']))
        except IntegrityError:
            pass  # Another replica won insertion; claim below is still atomic.
        with self.db.transaction() as session:
            item = session.scalar(select(WorkItem).where(WorkItem.job_id == data['job_id']).with_for_update())
            if item.status in ('completed', 'failed'):
                return None
            if item.lease_until and item.lease_until > now():
                raise Busy('Task already running')
            if item.next_attempt_at and item.next_attempt_at > now():
                raise Busy('Waiting for retry backoff')
            # A process killed during FFmpeg leaves a durable running attempt.
            if item.status == 'running':
                prior = session.scalar(select(Attempt).where(Attempt.job_id == item.job_id, Attempt.number == item.attempts))
                if prior:
                    prior.status, prior.error, prior.finished_at = 'failed', 'Worker lease expired', now()
            if item.attempts >= settings().max_attempts:
                self.fail_terminal(session, item, data, 'Worker lease expired; retry limit exhausted')
                return None
            item.attempts += 1
            item.status = 'running'
            item.lease_until = now() + timedelta(seconds=settings().processing_timeout + 60)
            item.next_attempt_at = None
            session.add(Attempt(job_id=item.job_id, number=item.attempts, status='running'))
            emit(session, 'task.finished', {'job_id': item.job_id, 'task': self.kind, 'status': 'running', 'attempts': item.attempts})
            return item.attempts

    def fail_terminal(self, session, item, data, error):
        item.status, item.error, item.lease_until = 'failed', error, None
        emit(session, 'task.finished', {'job_id': item.job_id, 'task': self.kind,
             'status': 'failed', 'attempts': item.attempts, 'error': error})
        emit(session, f'{self.service}.dead', {**data, 'error': error, 'attempts': item.attempts})

    def generate(self, data, attempt):
        source = media_path(data['path'])
        if not source.is_file():
            raise FileNotFoundError('Original video file is missing')
        directory = media_path(f"processed/{data['video_id']}/{data['job_id']}/attempt-{attempt}")
        directory.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        outputs = []
        if self.kind == 'transcode':
            for index, resolution in enumerate(data['resolutions']):
                target = directory / f'{resolution}p.mp4'
                temporary = directory / f'{resolution}p.partial.mp4'
                run_ffmpeg(['-i', str(source), '-map', '0:v:0', '-map', '0:a:0?',
                    '-vf', f'scale=-2:{resolution}', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(temporary)],
                    settings().processing_timeout - (time.monotonic() - started))
                temporary.replace(target)
                outputs.append({'resolution': resolution, 'path': target.relative_to(Path(settings().media_root).resolve()).as_posix(), 'size': target.stat().st_size})
                self.progress(data['job_id'], int((index + 1) / len(data['resolutions']) * 100))
        else:
            target = directory / 'thumbnail.jpg'
            temporary = directory / 'thumbnail.partial.jpg'
            run_ffmpeg(['-i', str(source), '-frames:v', '1', '-vf', 'scale=480:-2', '-q:v', '2', str(temporary)], settings().processing_timeout)
            temporary.replace(target)
            outputs.append({'path': target.relative_to(Path(settings().media_root).resolve()).as_posix()})
            self.progress(data['job_id'], 100)
        return outputs

    def handle(self, envelope):
        if envelope.get('version') != 1 or envelope['type'] not in (f'{self.kind}.requested', f'{self.service}.retry'):
            raise ValueError('Unsupported command')
        data = envelope['data']
        # Validate untrusted broker payload before claiming a job.
        from uuid import UUID
        UUID(data['job_id']); UUID(data['video_id'])
        if self.kind == 'transcode' and (not data['resolutions'] or len(data['resolutions']) > 3 or
                len(set(data['resolutions'])) != len(data['resolutions']) or any(x not in (360, 720, 1080) for x in data['resolutions'])):
            raise ValueError('Invalid resolutions')
        try:
            media_path(data['path'])
        except HTTPException as exc:
            raise ValueError('Invalid media path in command') from exc
        attempt = self.claim(data)
        if attempt is None:
            return
        try:
            outputs = self.generate(data, attempt)
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'[-4000:]
            log.exception('Processing task %s failed', data['job_id'])
            with self.db.transaction() as session:
                item = session.scalar(select(WorkItem).where(WorkItem.job_id == data['job_id']).with_for_update())
                if item.attempts != attempt or item.status != 'running':
                    return  # A recovered task owns this job now.
                row = session.scalar(select(Attempt).where(Attempt.job_id == item.job_id, Attempt.number == attempt))
                row.status, row.error, row.finished_at = 'failed', error, now()
                item.error, item.lease_until = error, None
                if attempt >= settings().max_attempts:
                    self.fail_terminal(session, item, data, error)
                else:
                    item.status = 'retrying'
                    item.next_attempt_at = now() + timedelta(milliseconds=settings().retry_delay_ms)
                    emit(session, f'{self.service}.retry', data)
                    emit(session, 'task.finished', {'job_id': item.job_id, 'task': self.kind, 'status': 'retrying', 'attempts': attempt, 'error': error})
            return
        with self.db.transaction() as session:
            item = session.scalar(select(WorkItem).where(WorkItem.job_id == data['job_id']).with_for_update())
            if item.attempts != attempt or item.status != 'running':
                return
            for output in outputs:
                model = Output if self.kind == 'transcode' else Thumbnail
                session.add(model(job_id=data['job_id'], video_id=data['video_id'], **output))
            row = session.scalar(select(Attempt).where(Attempt.job_id == item.job_id, Attempt.number == attempt))
            row.status, row.finished_at = 'completed', now()
            item.status, item.lease_until, item.error = 'completed', None, None
            emit(session, 'task.finished', {'job_id': item.job_id, 'task': self.kind, 'status': 'completed', 'attempts': attempt})

    def attempts(self, job_id):
        with self.db.transaction() as session:
            return [serialize(x) for x in session.scalars(select(Attempt).where(Attempt.job_id == job_id).order_by(Attempt.number))]
