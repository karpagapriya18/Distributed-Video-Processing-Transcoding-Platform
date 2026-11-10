from fastapi.testclient import TestClient
from sqlalchemy import select
from common.config import settings
from common.models import Outbox, Video
from services import upload
import json
from pathlib import Path
from types import SimpleNamespace
import pytest


def test_valid_upload_is_atomic_with_event(auth, fake_probe):
    with TestClient(upload.app) as client:
        response = client.post('/videos/upload', headers=auth, data={'title': 'Sample'}, files={'file': ('sample.mp4', b'video', 'video/mp4')})
    assert response.status_code == 201
    with upload.db.transaction() as session:
        video = session.get(Video, response.json()['id'])
        event = session.scalar(select(Outbox))
        assert video.size == 5
        assert event.topic == 'video.uploaded'
        assert event.payload['video_id'] == video.id


def test_invalid_format_is_rejected(auth):
    with TestClient(upload.app) as client:
        response = client.post('/videos/upload', headers=auth, data={'title': 'bad'}, files={'file': ('script.exe', b'bad', 'video/mp4')})
    assert response.status_code == 415


def test_oversized_video_leaves_no_record_or_file(auth, fake_probe, monkeypatch):
    monkeypatch.setattr(settings(), 'max_upload_bytes', 4)
    with TestClient(upload.app) as client:
        response = client.post('/videos/upload', headers=auth, data={'title': 'large'}, files={'file': ('v.mp4', b'12345', 'video/mp4')})
    assert response.status_code == 413
    with upload.db.transaction() as session:
        assert session.scalar(select(Video)) is None
        assert session.scalar(select(Outbox)) is None
    from pathlib import Path
    assert not list(Path(settings().media_root).rglob('*.upload'))


def test_empty_file_and_blank_title(auth):
    with TestClient(upload.app) as client:
        assert client.post('/videos/upload', headers=auth, data={'title': 'empty'}, files={'file': ('v.mp4', b'', 'video/mp4')}).status_code == 422
        assert client.post('/videos/upload', headers=auth, data={'title': '  '}, files={'file': ('v.mp4', b'x', 'video/mp4')}).status_code == 422


def test_disguised_content_rejected(auth, monkeypatch):
    from fastapi import HTTPException
    def invalid(path):
        raise HTTPException(422, 'Invalid video')
    monkeypatch.setattr(upload, 'probe', invalid)
    with TestClient(upload.app) as client:
        assert client.post('/videos/upload', headers=auth, data={'title': 'fake'}, files={'file': ('v.mp4', b'not a video', 'video/mp4')}).status_code == 422


def test_internal_endpoint_requires_credentials():
    with TestClient(upload.app) as client:
        assert client.get('/videos').status_code == 401


@pytest.mark.parametrize('container,accepted',[('mov,mp4,m4a,3gp,3g2,mj2',True),('matroska,webm',True),('gif',False)])
def test_probe_validates_actual_container_not_just_filename(monkeypatch,container,accepted):
    from common.media import probe
    from fastapi import HTTPException
    payload={'format':{'format_name':container,'duration':'1'},'streams':[{'codec_type':'video','width':320,'height':180}]}
    monkeypatch.setattr('common.media.subprocess.run',lambda *args,**kwargs:SimpleNamespace(stdout=json.dumps(payload)))
    if accepted:
        assert probe(Path('fixture.mp4'))['duration']==1
    else:
        with pytest.raises(HTTPException) as error:
            probe(Path('fixture.mp4'))
        assert error.value.status_code==422
