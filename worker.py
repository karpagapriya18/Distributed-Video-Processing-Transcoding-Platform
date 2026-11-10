from uuid import UUID
from fastapi import Depends, HTTPException
from sqlalchemy import select
from common.app import create_app
from common.db import Database
from common.models import Output, serialize
from common.processor import Processor
from common.security import internal

db = Database('worker')
processor = Processor(db, 'transcode')
app = create_app('worker', db, processor.handle)
auth = [Depends(internal)]


@app.get('/videos/{video_id}/outputs', dependencies=auth)
def outputs(video_id: UUID):
    with db.transaction() as session:
        return [serialize(x) for x in session.scalars(select(Output).where(Output.video_id == str(video_id), Output.status == 'completed').order_by(Output.created_at.desc()))]


@app.get('/outputs/{output_id}', dependencies=auth)
def output(output_id: UUID):
    with db.transaction() as session:
        row = session.get(Output, str(output_id))
        if not row:
            raise HTTPException(404, 'Output not found')
        return serialize(row)


@app.get('/jobs/{job_id}/attempts', dependencies=auth)
def attempts(job_id: UUID):
    return processor.attempts(str(job_id))
