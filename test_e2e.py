import asyncio
import json
import os
import subprocess
import time
from urllib.parse import urlsplit
from uuid import uuid4
import aio_pika
import httpx
import pytest

pytestmark=[pytest.mark.e2e,pytest.mark.skipif(os.getenv('RUN_E2E')!='1',reason='Set RUN_E2E=1 with the Compose stack running')]


def test_real_upload_broker_transcoding_and_playback(tmp_path):
    source=tmp_path/'sample.mp4'
    subprocess.run(['ffmpeg','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=10','-t','0.4','-c:v','libx264','-pix_fmt','yuv420p',str(source)],capture_output=True,check=True,timeout=30)
    base=os.getenv('TEST_API_URL','http://localhost:8000')
    key=os.getenv('API_KEY','local-demo-key-change-me')
    with httpx.Client(base_url=base,headers={'Authorization':f'Bearer {key}'},timeout=60) as client:
        with source.open('rb') as file:
            response=client.post('/videos/upload',data={'title':'E2E acceptance fixture'},files={'file':('sample.mp4',file,'video/mp4')})
        assert response.status_code==201,response.text
        video=response.json()
        response=client.post(f"/videos/{video['id']}/process",json={'resolutions':[360,720,1080]})
        assert response.status_code==202,response.text
        job=response.json()

        async def duplicate():
            connection=await aio_pika.connect_robust(os.getenv('RABBITMQ_URL','amqp://platform:platform@rabbitmq/'))
            async with connection:
                channel=await connection.channel(publisher_confirms=True)
                event={'id':str(uuid4()),'version':1,'type':'transcode.requested','data':{
                    'job_id':job['id'],'video_id':video['id'],'path':f"originals/{video['id']}.mp4",'resolutions':[360,720,1080]}}
                exchange=await channel.get_exchange('video.events')
                await exchange.publish(aio_pika.Message(json.dumps(event).encode(),delivery_mode=aio_pika.DeliveryMode.PERSISTENT),routing_key='transcode.requested')
        asyncio.run(duplicate())
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            state=client.get(f"/videos/{video['id']}/status").json()
            if state['status'] in ('completed','failed'):
                break
            time.sleep(1)
        assert state['status']=='completed',state
        metadata_deadline=time.monotonic()+30
        while time.monotonic()<metadata_deadline:
            detail=client.get(f"/videos/{video['id']}").json()
            if detail.get('metadata') and detail['metadata']['status']=='completed':
                break
            time.sleep(1)
        assert detail['metadata']['width']==320 and detail['metadata']['status']=='completed',detail
        info=client.get(f"/jobs/{job['id']}").json()
        assert len(info['processing_attempts']['transcode'])==1,info
        assert info['progress']==100
        outputs=client.get(f"/videos/{video['id']}/outputs").json()
        assert {x['resolution'] for x in outputs}=={360,720,1080}
        thumbnails=client.get(f"/videos/{video['id']}/thumbnails").json()
        assert len(thumbnails)==1
        for item in [*outputs,*thumbnails]:
            url=urlsplit(item['url'])
            # PUBLIC_BASE_URL is browser-facing; use the test container's gateway host.
            path=f'{url.path}?{url.query}'
            content=client.get(path)
            assert content.status_code==200 and len(content.content)>100
        url=urlsplit(outputs[0]['url'])
        partial=client.get(f'{url.path}?{url.query}',headers={'Range':'bytes=0-99'})
        assert partial.status_code==206 and len(partial.content)==100
        assert client.get(f'{url.path}?expires=1&signature=bad').status_code==403
        assert any(v['id']==video['id'] for v in client.get('/videos').json())


def test_e2e_rejects_fake_video():
    with httpx.Client(base_url=os.getenv('TEST_API_URL','http://localhost:8000'),headers={'Authorization':f"Bearer {os.getenv('API_KEY','local-demo-key-change-me')}"}) as client:
        response=client.post('/videos/upload',data={'title':'fake'},files={'file':('fake.mp4',b'not video','video/mp4')})
        assert response.status_code==422
