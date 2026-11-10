from fastapi.testclient import TestClient
from common.models import Job, uid
from common.config import settings
from services import orchestrator, gateway


def seed_jobs():
    video = uid()
    with orchestrator.db.transaction() as session:
        for status in ['completed', 'failed', 'queued']:
            session.add(Job(video_id=video, status=status, resolutions=[360]))
        session.add(Job(video_id=uid(), status='completed', resolutions=[720]))
    return video


def test_filtered_history_pagination_and_counts(auth):
    video = seed_jobs()
    with TestClient(orchestrator.app) as client:
        response = client.get('/jobs', params={'video_id': video, 'limit': 2}, headers=auth)
        assert response.status_code == 200
        body = response.json()
        assert body['total'] == 3 and len(body['items']) == 2
        page2 = client.get('/jobs', params={'video_id': video, 'limit': 2, 'offset': 2}, headers=auth).json()
        assert len(page2['items']) == 1
        assert not ({x['id'] for x in body['items']} & {x['id'] for x in page2['items']})
        failed = client.get('/jobs?status=failed', headers=auth).json()
        assert failed['total'] == 1 and failed['items'][0]['status'] == 'failed'
        summary = client.get('/operations/summary', headers=auth).json()
        assert summary['total_jobs'] == 4 and summary['active_jobs'] == 1
        assert summary['success_rate'] == 2 / 3


def test_empty_summary_and_validation(auth):
    with TestClient(orchestrator.app) as client:
        assert client.get('/operations/summary', headers=auth).json()['success_rate'] is None
        for query in ['limit=0', 'limit=101', 'offset=-1', 'status=bogus', 'video_id=bad']:
            assert client.get('/jobs?' + query, headers=auth).status_code == 422
        assert client.get('/jobs').status_code == 401
        assert client.get('/operations/summary').status_code == 401


def test_gateway_forwards_filters_and_checks_video(monkeypatch):
    calls = []
    async def fake_upstream(base, path, **kwargs):
        calls.append((path, kwargs))
        return {'items': [], 'total': 0}
    monkeypatch.setattr(gateway, 'upstream', fake_upstream)
    auth = {'Authorization': f'Bearer {settings().api_key}'}
    video = uid()
    with TestClient(gateway.app) as client:
        assert client.get('/jobs').status_code == 401
        assert client.get('/operations/summary').status_code == 401
        assert client.get('/jobs?status=failed&limit=10', headers=auth).status_code == 200
        assert calls[-1] == ('/jobs', {'params': {'limit': 10, 'offset': 0, 'status': 'failed'}})
        assert client.get(f'/videos/{video}/jobs', headers=auth).status_code == 200
        assert calls[-2][0] == f'/videos/{video}'
        assert calls[-1][1]['params']['video_id'] == video
