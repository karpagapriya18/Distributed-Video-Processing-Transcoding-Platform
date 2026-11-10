import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from common.bus import Bus
from common.db import emit
from common.models import Outbox, uid
from services import upload


@pytest.mark.asyncio
@pytest.mark.parametrize('publish_succeeds',[True,False])
async def test_outbox_marks_sent_only_after_confirmed_publication(monkeypatch,publish_succeeds):
    with upload.db.transaction() as session:
        emit(session,'video.uploaded',{'video_id':uid()})
    bus=Bus(upload.db)
    bus.exchange=SimpleNamespace(publish=AsyncMock(side_effect=None if publish_succeeds else ConnectionError('broker unavailable')))
    async def stop_after_one_pass(*args):
        raise asyncio.CancelledError
    monkeypatch.setattr(asyncio,'sleep',stop_after_one_pass)
    with pytest.raises(asyncio.CancelledError):
        await bus.relay()
    with upload.db.transaction() as session:
        row=session.scalar(select(Outbox))
        assert (row.published_at is not None)==publish_succeeds
    assert bus.exchange.publish.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize('retry_count',[0,5,-1,'invalid'])
@pytest.mark.parametrize('failure,publish_fails,expected',[
    (None,False,'ack'),(ValueError('bad command'),False,'reject'),
    (ConnectionError('database unavailable'),False,'ack'),
    (ConnectionError('database unavailable'),True,'nack')])
async def test_consumer_acknowledgement_and_delayed_redelivery(monkeypatch,failure,publish_fails,expected,retry_count):
    queues={}
    class Queue:
        def __init__(self,name):
            self.name=name
        async def bind(self,*args,**kwargs):
            pass
        async def consume(self,callback):
            self.callback=callback
    async def declare_queue(name,**kwargs):
        queues[name]=Queue(name)
        return queues[name]
    channel=SimpleNamespace(set_qos=AsyncMock(),declare_exchange=AsyncMock(),declare_queue=declare_queue,
        default_exchange=SimpleNamespace(publish=AsyncMock(side_effect=ConnectionError('publish failed') if publish_fails else None)))
    connection=SimpleNamespace(channel=AsyncMock(return_value=channel),close=AsyncMock())
    monkeypatch.setattr('common.bus.aio_pika.connect_robust',AsyncMock(return_value=connection))
    def handler(envelope):
        if failure:
            raise failure
    bus=Bus()
    await bus.start('worker',handler)
    message=SimpleNamespace(headers={'x-consumer-retries':retry_count},message_id='original-id',correlation_id='trace-id',content_type='application/json',body=json.dumps({'id':uid(),'version':1,'type':'transcode.requested','data':{}}).encode(),ack=AsyncMock(),reject=AsyncMock(),nack=AsyncMock())
    if isinstance(failure, ConnectionError) and retry_count != 0:
        expected = 'reject'
    await queues['worker'].callback(message)
    assert getattr(message,expected).await_count==1
    if isinstance(failure,ConnectionError) and retry_count == 0:
        assert channel.default_exchange.publish.await_args.kwargs['routing_key']=='worker.retry'
        sent = channel.default_exchange.publish.await_args.args[0]
        assert sent.headers['x-consumer-retries'] == 1
        assert sent.message_id == 'original-id' and sent.correlation_id == 'trace-id'
    if expected=='reject':
        channel.default_exchange.publish.assert_not_awaited()
        assert message.reject.await_args.kwargs=={'requeue':False}
    await bus.stop()
