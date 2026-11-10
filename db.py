import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from common.models import Outbox


class Database:
    def __init__(self, service: str):
        self.service = service
        url = os.getenv(f'{service.upper()}_DATABASE_URL',
                        f'mysql+pymysql://{service}:{service}@mysql/{service}')
        self.engine = create_engine(url, pool_pre_ping=True,
                                    **({'connect_args': {'check_same_thread': False}} if url.startswith('sqlite') else {}))
        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self):
        with self.factory.begin() as session:
            yield session

    def session(self):
        with self.factory() as session:
            yield session


def emit(session, topic: str, payload: dict):
    session.add(Outbox(topic=topic, payload=payload))
