"""Contract integration with isolated service databases and in-process HTTP.

RabbitMQ delivery itself is covered by test_e2e.py against the running stack.
"""
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from common.models import Inbox, Outbox, VideoMetadata, uid
from services import delivery, gateway, metadata, orchestrator, thumbnail, upload, worker


def envelope(row):
    return {'id':row.id,'version':1,'type':row.topic,'data':row.payload}


def events(db, topic):
    with db.transaction() as session:
        return [envelope(row) for row in session.scalars(select(Outbox).where(Outbox.topic == topic))]


def test_upload_metadata_job_workers_and_completion(auth, fake_probe, monkeypatch):
    with TestClient(upload.app) as client:
        video=client.post('/videos/upload',headers=auth,data={'title':'Contract integration'},files={'file':('v.mp4',b'video','video/mp4')}).json()
    uploaded=events(upload.db,'video.uploaded')[0]
    metadata.handle(uploaded)
    metadata.handle(uploaded)
    with metadata.db.transaction() as session:
        assert len(list(session.scalars(select(VideoMetadata))))==1
        assert len(list(session.scalars(select(Inbox))))==1
    job=orchestrator.create_job(video,orchestrator.ProcessRequest(resolutions=[360]))
    monkeypatch.setattr(worker.processor,'generate',lambda *args:[{'resolution':360,'path':'processed/360.mp4','size':100}])
    monkeypatch.setattr(thumbnail.processor,'generate',lambda *args:[{'path':'processed/thumbnail.jpg'}])
    worker.processor.handle(events(orchestrator.db,'transcode.requested')[0])
    thumbnail.processor.handle(events(orchestrator.db,'thumbnail.requested')[0])
    for db in (worker.db,thumbnail.db):
        for event in events(db,'task.finished'):
            orchestrator.handle(event)
    for event in events(orchestrator.db,'video.status'):
        metadata.handle(event)
    # An older status delivered after completion cannot overwrite newer metadata.
    metadata.handle({**events(orchestrator.db,'video.status')[0],'id':uid()})
    with TestClient(orchestrator.app) as client:
        status=client.get(f"/jobs/{job['id']}",headers=auth).json()
        assert status['status']=='completed' and status['progress']==100
    with TestClient(metadata.app) as client:
        assert client.get(f"/videos/{video['id']}",headers=auth).json()['status']=='completed'


@pytest.mark.asyncio
async def test_gateway_routes_and_delivers_signed_catalog(auth, fake_probe, monkeypatch):
    with TestClient(upload.app) as client:
        video=client.post('/videos/upload',headers=auth,data={'title':'Gateway'},files={'file':('v.mp4',b'video','video/mp4')}).json()
    job=orchestrator.create_job(video,orchestrator.ProcessRequest(resolutions=[360]))
    monkeypatch.setattr(worker.processor,'generate',lambda *args:[{'resolution':360,'path':'processed/360.mp4','size':100}])
    worker.processor.handle(events(orchestrator.db,'transcode.requested')[0])
    real_client=httpx.AsyncClient
    applications={'upload':upload.app,'metadata':metadata.app,'orchestrator':orchestrator.app,'worker':worker.app,'thumbnail':thumbnail.app,'delivery':delivery.app}

    class ServiceTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self,request):
            return await httpx.ASGITransport(app=applications[request.url.host]).handle_async_request(request)

    monkeypatch.setattr(httpx,'AsyncClient',lambda *args,**kwargs:real_client(*args,**{**kwargs,'transport':ServiceTransport()}))
    from common.config import settings
    async with real_client(transport=httpx.ASGITransport(app=gateway.app),base_url='http://test') as client:
        public={'Authorization':f'Bearer {settings().api_key}'}
        assert (await client.get('/videos')).status_code==401
        accepted=await client.post('/videos/upload',headers=public,data={'title':'Through gateway'},files={'file':('via-gateway.mp4',b'valid fixture','video/mp4')})
        assert accepted.status_code==201 and 'path' not in accepted.json()
        listed=await client.get('/videos',headers=public)
        assert listed.status_code==200 and 'path' not in listed.json()[0]
        response=await client.get(f"/videos/{video['id']}/outputs",headers=public)
        assert response.status_code==200
        assert response.json()[0]['resolution']==360
        assert 'signature=' in response.json()[0]['url']
        assert 'path' not in response.json()[0]
        detail=await client.get(f"/jobs/{job['id']}",headers=public)
        assert len(detail.json()['processing_attempts']['transcode'])==1


def test_gateway_rejects_oversized_body_before_forwarding():
    from common.config import settings
    with TestClient(gateway.app) as client:
        response=client.post('/videos/upload',headers={
            'Authorization':f'Bearer {settings().api_key}',
            'Content-Length':str(settings().max_upload_bytes+2*1024*1024)})
    assert response.status_code==413
