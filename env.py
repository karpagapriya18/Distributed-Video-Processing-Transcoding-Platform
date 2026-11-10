from alembic import context
from sqlalchemy import MetaData
from common.db import Database
from common.models import OWNERS

service = context.get_x_argument(as_dictionary=True).get('service')
if service not in OWNERS or not OWNERS[service]:
    raise RuntimeError('Use -x service=upload|metadata|orchestrator|worker|thumbnail')
target = MetaData()
for model in OWNERS[service]:
    model.__table__.to_metadata(target)
context.config.attributes['owned_metadata'] = target
if context.is_offline_mode():
    context.configure(url=str(Database(service).engine.url), target_metadata=target, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with Database(service).engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target)
        with context.begin_transaction():
            context.run_migrations()
