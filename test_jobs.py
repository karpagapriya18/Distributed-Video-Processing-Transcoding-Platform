import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from common.contracts import ProcessRequest
from common.models import Job, Outbox, uid
from services import orchestrator


def test_job_creation_persists_both_tasks():
    video = {'id': uid(), 'path': 'originals/video.mp4'}
    job = orchestrator.create_job(video, ProcessRequest(resolutions=[360, 720]))
    assert job['status'] == 'queued'
    with orchestrator.db.transaction() as session:
        assert session.get(Job, job['id']).resolutions == [360, 720]
        topics = [x.topic for x in session.scalars(select(Outbox))]
        assert 'transcode.requested' in topics and 'thumbnail.requested' in topics
    with pytest.raises(HTTPException) as error:
        orchestrator.create_job(video, ProcessRequest())
    assert error.value.status_code == 409


@pytest.mark.parametrize('resolutions', [[480], [360,360], [], [360,720,1080,360]])
def test_invalid_resolution_request(resolutions):
    with pytest.raises(ValidationError):
        ProcessRequest(resolutions=resolutions)


def test_duplicate_and_out_of_order_results_do_not_regress():
    job = orchestrator.create_job({'id': uid(), 'path': 'originals/v.mp4'}, ProcessRequest())
    def event(task, status):
        return {'id':uid(),'version':1,'type':'task.finished','data':{'job_id':job['id'],'task':task,'status':status,'attempts':1}}
    completed = event('transcode','completed')
    orchestrator.handle(completed)
    orchestrator.handle(completed)
    orchestrator.handle(event('transcode','running'))
    orchestrator.handle(event('thumbnail','completed'))
    with orchestrator.db.transaction() as session:
        row = session.get(Job,job['id'])
        assert row.status == 'completed' and row.progress == 100
        assert row.active_video_id is None


def test_failed_job_waits_for_both_tasks_and_can_be_retried():
    video = {'id':uid(),'path':'originals/v.mp4'}
    job = orchestrator.create_job(video, ProcessRequest())
    for task,status in [('transcode','failed'),('thumbnail','completed')]:
        orchestrator.handle({'id':uid(),'version':1,'type':'task.finished','data':{'job_id':job['id'],'task':task,'status':status,'attempts':3,'error':'encoder failed' if status=='failed' else None}})
    with orchestrator.db.transaction() as session:
        assert session.get(Job, job['id']).status == 'failed'
    assert orchestrator.create_job(video, ProcessRequest())['id'] != job['id']
