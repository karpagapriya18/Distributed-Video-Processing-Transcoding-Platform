from uuid import UUID
from fastapi import Depends, HTTPException
from sqlalchemy import select
from common.app import create_app
from common.db import Database
from common.models import Thumbnail, serialize
from common.processor import Processor
from common.security import internal

db = Database('thumbnail')
processor = Processor(db, 'thumbnail')
app = create_app('thumbnail', db, processor.handle)
auth = [Depends(internal)]


@app.get('/videos/{video_id}/thumbnails', dependencies=auth)
def thumbnails(video_id: UUID):
    with db.transaction() as session:
        return [serialize(x) for x in session.scalars(select(Thumbnail).where(Thumbnail.video_id == str(video_id), Thumbnail.status == 'completed').order_by(Thumbnail.created_at.desc()))]


@app.get('/thumbnails/{thumbnail_id}', dependencies=auth)
def thumbnail(thumbnail_id: UUID):
    with db.transaction() as session:
        row = session.get(Thumbnail, str(thumbnail_id))
        if not row:
            raise HTTPException(404, 'Thumbnail not found')
        return serialize(row)


@app.get('/jobs/{job_id}/attempts', dependencies=auth)
def attempts(job_id: UUID):
    return processor.attempts(str(job_id))
