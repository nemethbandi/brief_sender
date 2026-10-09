import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from storage.mssql import metadata


@pytest.fixture
def storage_engine():
    # Only tests create tables. SQLite exercises transactional/data contracts;
    # SQL Server-specific compilation and live integration are separate checks.
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False},
                           execution_options={"schema_translate_map": {"dbo": None}})
    metadata.create_all(engine)
    yield engine
    engine.dispose()
