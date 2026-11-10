from uuid import UUID
from datetime import datetime
from fastapi import Depends, HTTPException
from sqlalchemy import select
from common.app import create_app
from common.db import Database
from common.models import Inbox, VideoMetadata, serialize
from common.security import internal

db = Database('metadata')


def handle(envelope):
    if envelope.get('version') != 1 or envelope['type'] not in ('video.uploaded', 'video.status'):
        raise ValueError('Unsupported event')
    data = envelope['data']
    with db.transaction() as session:
        if session.scalar(select(Inbox).where(Inbox.event_id == envelope['id'])):
            return
        session.add(Inbox(event_id=envelope['id']))
        row = session.scalar(select(VideoMetadata).where(VideoMetadata.video_id == data['video_id']).with_for_update())
        if envelope['type'] == 'video.status':
            if not row:
                raise RuntimeError('Upload event has not arrived yet')
            changed_at = datetime.fromisoformat(data['changed_at'])
            if not row.status_changed_at or changed_at > row.status_changed_at:
                row.status, row.error, row.status_changed_at = data['status'], data.get('error'), changed_at
        elif not row:
            session.add(VideoMetadata(**{k: data[k] for k in ('video_id', 'title', 'format', 'duration', 'width', 'height', 'size')}))


app = create_app('metadata', db, handle)


@app.get('/videos/{video_id}', dependencies=[Depends(internal)])
def get_metadata(video_id: UUID):
    with db.transaction() as session:
        row = session.scalar(select(VideoMetadata).where(VideoMetadata.video_id == str(video_id)))
        if not row:
            raise HTTPException(404, 'Metadata not yet available')
        return serialize(row)
