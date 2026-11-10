import os
import tempfile
from pathlib import Path

test_root = Path(tempfile.mkdtemp(prefix='frameflow-tests-'))
os.environ['DISABLE_BROKER'] = 'true'
os.environ['MEDIA_ROOT'] = str(test_root / 'media')
for name in ('upload', 'metadata', 'orchestrator', 'worker', 'thumbnail'):
    os.environ[f'{name.upper()}_DATABASE_URL'] = f'sqlite:///{(test_root / (name + ".db")).as_posix()}'

import pytest
from common.config import settings
from common.models import Base, OWNERS
from services import upload, metadata, orchestrator, worker, thumbnail

SERVICES = [upload, metadata, orchestrator, worker, thumbnail]


@pytest.fixture(autouse=True)
def isolated_databases(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), 'media_root', str(tmp_path / 'media'))
    monkeypatch.setattr(settings(), 'retry_delay_ms', 0)
    for service in SERVICES:
        tables = [m.__table__ for m in OWNERS[service.db.service]]
        Base.metadata.drop_all(service.db.engine, tables=tables)
        Base.metadata.create_all(service.db.engine, tables=tables)
    yield


@pytest.fixture
def auth():
    return {'X-Internal-Token': settings().internal_token}


@pytest.fixture
def fake_probe(monkeypatch):
    value = {'format': 'mov,mp4', 'duration': 2.0, 'width': 320, 'height': 180}
    monkeypatch.setattr(upload, 'probe', lambda path: value)
    return value
