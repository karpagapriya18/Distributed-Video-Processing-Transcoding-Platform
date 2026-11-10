import asyncio
import json
import logging
from datetime import timedelta
import aio_pika
from sqlalchemy import select
from common.config import settings
from common.models import Outbox, now

log = logging.getLogger(__name__)
ROUTES = {
    'metadata': ['video.uploaded', 'video.status'],
    'orchestrator': ['task.finished'],
    'worker': ['transcode.requested'],
    'thumbnail': ['thumbnail.requested'],
}


class Bus:
    def __init__(self, db=None):
        self.db = db
        self.connection = None
        self.channel = None
        self.task = None

    async def start(self, service: str, handler=None):
        self.connection = await aio_pika.connect_robust(settings().rabbitmq_url)
        self.channel = await self.connection.channel(publisher_confirms=True, on_return_raises=True)
        await self.channel.set_qos(prefetch_count=1)
        self.exchange = await self.channel.declare_exchange('video.events', 'topic', durable=True)
        dead = await self.channel.declare_exchange('video.dead', 'topic', durable=True)
        # Declare all routes before publishing: upload can arrive before subscribers start.
        for name, routes in ROUTES.items():
            dlq = await self.channel.declare_queue(f'{name}.dead', durable=True)
            await dlq.bind(dead, routing_key=name)
            queue = await self.channel.declare_queue(name, durable=True, arguments={
                'x-dead-letter-exchange': 'video.dead', 'x-dead-letter-routing-key': name})
            for route in routes:
                await queue.bind(self.exchange, routing_key=route)
            retry = await self.channel.declare_queue(f'{name}.retry', durable=True, arguments={
                'x-dead-letter-exchange': '', 'x-dead-letter-routing-key': name})
            if name == service and handler:
                async def callback(message, handler=handler, retry=retry):
                    try:
                        envelope = json.loads(message.body)
                        await asyncio.to_thread(handler, envelope)
                    except (ValueError, KeyError, TypeError):
                        log.exception('Malformed or unsupported message')
                        await message.reject(requeue=False)
                    except Exception:
                        log.exception('Consumer failed')
                        retry_headers = dict(getattr(message, 'headers', None) or {})
                        count = retry_headers.get('x-consumer-retries', 0)
                        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                            await message.reject(requeue=False)
                            return
                        if count >= settings().consumer_max_retries:
                            log.error('Consumer retry limit reached; dead-lettering message')
                            await message.reject(requeue=False)
                            return
                        retry_headers['x-consumer-retries'] = count + 1
                        try:
                            await self.channel.default_exchange.publish(aio_pika.Message(
                                message.body, headers=retry_headers,
                                message_id=getattr(message, 'message_id', None),
                                correlation_id=getattr(message, 'correlation_id', None),
                                content_type=getattr(message, 'content_type', None),
                                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                expiration=timedelta(milliseconds=settings().retry_delay_ms)), routing_key=retry.name)
                            await message.ack()
                        except Exception:
                            await message.nack(requeue=True)
                    else:
                        await message.ack()
                await queue.consume(callback)
        if self.db:
            self.task = asyncio.create_task(self.relay())

    def pending(self):
        with self.db.transaction() as session:
            return [(x.id, x.topic, x.payload) for x in session.scalars(
                select(Outbox).where(Outbox.published_at.is_(None)).order_by(Outbox.created_at).limit(50))]

    def mark_sent(self, event_id):
        with self.db.transaction() as session:
            row = session.get(Outbox, event_id)
            row.published_at = now()

    async def relay(self):
        while True:
            try:
                for event_id, topic, payload in await asyncio.to_thread(self.pending):
                    envelope = {'id': event_id, 'version': 1, 'type': topic, 'data': payload}
                    message = aio_pika.Message(json.dumps(envelope).encode(),
                        message_id=event_id, content_type='application/json',
                        expiration=timedelta(milliseconds=settings().retry_delay_ms) if topic.endswith('.retry') else None,
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
                    # Retry and exhausted-task messages go directly to service queues.
                    if topic.endswith('.retry') or topic.endswith('.dead'):
                        await self.channel.default_exchange.publish(message, routing_key=topic)
                    else:
                        await self.exchange.publish(message, routing_key=topic, mandatory=True)
                    await asyncio.to_thread(self.mark_sent, event_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('Outbox relay will retry; event remains durable')
            await asyncio.sleep(1)

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.connection:
            await self.connection.close()
