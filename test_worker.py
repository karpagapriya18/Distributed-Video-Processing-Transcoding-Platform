from datetime import timedelta
import pytest
from sqlalchemy import select
from common.config import settings
from common.models import Attempt, Outbox, Output, WorkItem, now, uid
from common.processor import Busy
from services import worker


def command():
    return {'id':uid(),'version':1,'type':'transcode.requested','data':{'job_id':uid(),'video_id':uid(),'path':'originals/v.mp4','resolutions':[360]}}


def test_duplicate_message_generates_only_once(monkeypatch):
    calls=[]
    monkeypatch.setattr(worker.processor,'generate',lambda data,attempt: calls.append(attempt) or [{'resolution':360,'path':'processed/v.mp4','size':100}])
    event=command()
    worker.processor.handle(event)
    worker.processor.handle(event)
    with worker.db.transaction() as session:
        assert len(list(session.scalars(select(Output)))) == 1
        assert len(list(session.scalars(select(Attempt)))) == 1
    assert calls == [1]


def test_worker_failure_retries_then_dead_letters(monkeypatch):
    def fail(*args):
        raise RuntimeError('Encoder crashed')
    monkeypatch.setattr(worker.processor,'generate',fail)
    event=command()
    for _ in range(settings().max_attempts + 1):
        worker.processor.handle(event)
    with worker.db.transaction() as session:
        item=session.scalar(select(WorkItem))
        assert item.status == 'failed' and item.attempts == 3
        attempts=list(session.scalars(select(Attempt)))
        assert len(attempts)==3 and all(a.error for a in attempts)
        topics=[e.topic for e in session.scalars(select(Outbox))]
        assert topics.count('worker.retry')==2
        assert topics.count('worker.dead')==1


def test_concurrent_duplicate_is_delayed():
    event=command()
    assert worker.processor.claim(event['data'])==1
    with pytest.raises(Busy):
        worker.processor.handle(event)


def test_expired_worker_lease_is_recovered(monkeypatch):
    event=command()
    worker.processor.claim(event['data'])
    with worker.db.transaction() as session:
        session.scalar(select(WorkItem)).lease_until=now()-timedelta(seconds=1)
    monkeypatch.setattr(worker.processor,'generate',lambda data,attempt:[{'resolution':360,'path':'processed/v.mp4','size':1}])
    worker.processor.handle(event)
    with worker.db.transaction() as session:
        attempts=list(session.scalars(select(Attempt).order_by(Attempt.number)))
        assert [a.status for a in attempts]==['failed','completed']
        assert attempts[0].error=='Worker lease expired'


def test_backoff_cannot_be_bypassed_by_duplicate(monkeypatch):
    monkeypatch.setattr(settings(),'retry_delay_ms',10000)
    monkeypatch.setattr(worker.processor,'generate',lambda *args: (_ for _ in ()).throw(RuntimeError('fail')))
    event=command()
    worker.processor.handle(event)
    with pytest.raises(Busy):
        worker.processor.handle(event)
