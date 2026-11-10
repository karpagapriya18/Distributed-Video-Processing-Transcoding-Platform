from pathlib import Path
from uuid import UUID
from fastapi import Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from common.app import create_app
from common.config import settings
from common.db import Database, emit
from common.media import probe, validate_upload
from common.models import Video, serialize, uid
from common.security import internal, media_path

db = Database('upload')
app = create_app('upload', db)
auth = [Depends(internal)]


@app.post('/videos/upload', status_code=201, dependencies=auth)
def upload(file: UploadFile = File(...), title: str = Form(..., min_length=1, max_length=200)):
    title = title.strip()
    if not title:
        raise HTTPException(422, 'Title cannot be blank')
    filename = Path((file.filename or '').replace('\\', '/')).name
    # Validate headers before writing any data. Actual content is checked by ffprobe.
    validate_upload(filename, file.content_type or '', 1)
    video_id = uid()
    relative = f'originals/{video_id}{Path(filename).suffix.lower()}'
    final = media_path(relative)
    final.parent.mkdir(parents=True, exist_ok=True)
    temporary = final.with_suffix('.upload')
    size = 0
    committed = False
    try:
        with temporary.open('wb') as target:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                validate_upload(filename, file.content_type or '', size)
                target.write(chunk)
        validate_upload(filename, file.content_type or '', size)
        info = probe(temporary)
        temporary.replace(final)
        with db.transaction() as session:
            video = Video(id=video_id, title=title, filename=filename, path=relative, size=size)
            session.add(video)
            session.flush()
            emit(session, 'video.uploaded', {'video_id': video_id, 'title': title, 'size': size, **info})
            result = serialize(video)
        committed = True
        return result
    finally:
        file.file.close()
        temporary.unlink(missing_ok=True)
        if not committed:
            final.unlink(missing_ok=True)


@app.get('/videos', dependencies=auth)
def list_videos(limit: int = 50, offset: int = 0):
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(422, 'limit must be 1–100 and offset must be nonnegative')
    with db.transaction() as session:
        return [serialize(v) for v in session.scalars(select(Video).order_by(Video.created_at.desc()).limit(limit).offset(offset))]


@app.get('/videos/{video_id}', dependencies=auth)
def get_video(video_id: UUID):
    with db.transaction() as session:
        video = session.get(Video, str(video_id))
        if not video:
            raise HTTPException(404, 'Video not found')
        return serialize(video)
