import os
import subprocess
import sys
from pathlib import Path
import pytest
from sqlalchemy import MetaData, create_engine, inspect
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from common.models import OWNERS


def test_mysql_timestamps_preserve_event_order_within_a_second():
    from sqlalchemy import DateTime
    from sqlalchemy.dialects import mysql
    for models in OWNERS.values():
        for model in models:
            for column in model.__table__.columns:
                if isinstance(column.type,DateTime):
                    assert column.type.dialect_impl(mysql.dialect()).fsp==6


@pytest.mark.parametrize('service',['upload','metadata','orchestrator','worker','thumbnail'])
def test_upgrade_is_idempotent_and_only_creates_owned_tables(service,tmp_path):
    url=f'sqlite:///{(tmp_path / "migration.db").as_posix()}'
    env={**os.environ,f'{service.upper()}_DATABASE_URL':url}
    root=Path(__file__).resolve().parents[1]
    command=[sys.executable,'-m','alembic','-x',f'service={service}','upgrade','head']
    for _ in range(2):
        result=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr
    engine=create_engine(url)
    assert set(inspect(engine).get_table_names())=={m.__tablename__ for m in OWNERS[service]}|{'alembic_version'}
    target=MetaData()
    for model in OWNERS[service]:
        model.__table__.to_metadata(target)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection),target)==[]
    result=subprocess.run([sys.executable,'-m','alembic','-x',f'service={service}','downgrade','base'],cwd=root,env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert inspect(engine).get_table_names()==['alembic_version']
    engine.dispose()
