import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from common.bus import Bus
from common.config import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


def create_app(name: str, db=None, handler=None):
    bus = Bus(db)

    @asynccontextmanager
    async def lifespan(app):
        if not settings().disable_broker and (db or handler):
            await bus.start(name, handler)
        yield
        await bus.stop()

    app = FastAPI(title=f'Video Platform — {name}', version='1.0.0', lifespan=lifespan)

    @app.get('/health', tags=['operations'])
    def health():
        if db:
            with db.engine.connect() as connection:
                connection.execute(text('SELECT 1'))
            if not settings().disable_broker and (not bus.connection or bus.connection.is_closed):
                raise HTTPException(503, 'Broker disconnected')
        return {'service': name, 'status': 'ok'}

    return app
