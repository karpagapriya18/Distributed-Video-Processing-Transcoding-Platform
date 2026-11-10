import time
from fastapi.testclient import TestClient
from common.models import uid
from common.security import media_path, signature
from services import delivery
from services import gateway
import httpx
import pytest


def test_delivery_checks_signature_and_serves_byte_ranges(monkeypatch):
    item_id=uid()
    path=media_path('processed/sample.mp4')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b'0123456789'*100)
    async def fetch(url):
        return {'id':item_id,'path':'processed/sample.mp4','status':'completed'}
    monkeypatch.setattr(delivery,'fetch',fetch)
    expires=int(time.time())+60
    params={'expires':expires,'signature':signature('outputs',item_id,expires)}
    with TestClient(delivery.app) as client:
        full=client.get(f'/media/outputs/{item_id}',params=params)
        assert full.status_code==200 and len(full.content)==1000
        part=client.get(f'/media/outputs/{item_id}',params=params,headers={'Range':'bytes=0-9'})
        assert part.status_code==206 and part.content==b'0123456789'
        assert part.headers['content-range']=='bytes 0-9/1000'
        assert client.get(f'/media/outputs/{item_id}',params={**params,'signature':'bad'}).status_code==403


@pytest.mark.asyncio
async def test_gateway_streams_signed_media_and_preserves_ranges(monkeypatch):
    item_id=uid()
    path=media_path('processed/proxied.mp4')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b'abcdefghij'*100)
    async def fetch(url):
        return {'id':item_id,'path':'processed/proxied.mp4','status':'completed'}
    monkeypatch.setattr(delivery,'fetch',fetch)
    real_client=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real_client(**kwargs,transport=httpx.ASGITransport(app=delivery.app)))
    expires=int(time.time())+60
    params={'expires':expires,'signature':signature('outputs',item_id,expires)}
    async with real_client(transport=httpx.ASGITransport(app=gateway.app),base_url='http://test') as client:
        response=await client.get(f'/media/outputs/{item_id}',params=params,headers={'Range':'bytes=10-19'})
        assert response.status_code==206 and response.content==b'abcdefghij'
        assert response.headers['content-range']=='bytes 10-19/1000'
        assert (await client.get(f'/media/outputs/{item_id}',params={**params,'signature':'bad'})).status_code==403
