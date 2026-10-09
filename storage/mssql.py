"""Shared MSSQL connection and table mappings; never runs DDL."""
from contextlib import contextmanager
from importlib import import_module
import os

from sqlalchemy import (
    Column, Date, DateTime, Float, Integer, MetaData, Table, Unicode, text,
)

metadata = MetaData(schema="dbo")
ticker_type = Unicode(128).with_variant(
    Unicode(128, collation="Latin1_General_100_BIN2"), "mssql",
)
rankings = Table(
    "momentum_rankings", metadata,
    Column("as_of_date", Date, primary_key=True),
    Column("ticker", ticker_type, primary_key=True),
    Column("rank", Integer, nullable=False),
    *[Column(name, Float) for name in (
        "return_6m", "return_12m", "vol_6m", "vol_12m", "score_6m", "score_12m",
    )],
    Column("momentum_score", Float, nullable=False),
    Column("sector", Unicode(256)), Column("industry", Unicode(512)),
    Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
security_metadata = Table(
    "security_metadata", metadata,
    Column("ticker", ticker_type, primary_key=True),
    Column("sector", Unicode(256), nullable=False),
    Column("industry", Unicode(512), nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
sent_reports = Table(
    "sent_reports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", DateTime, nullable=False),
    Column("recipient", Unicode(2048), nullable=False),
    Column("subject", Unicode(1024), nullable=False),
    Column("article_count", Integer, nullable=False),
    Column("mode", Unicode(64), nullable=False),
)


def create_storage_engine():
    """Use the deployed adapter and its own adjacent credentials JSON."""
    module_name = os.getenv("BRIEF_DB_ADAPTER_MODULE", "Common.DB_adapter.DB_request")
    key = os.getenv("BRIEF_DB_CONNECTION", "Analyst_TEST2_Airflow")
    try:
        adapter = import_module(module_name)
    except ImportError:
        raise RuntimeError(
            f"Cannot import DB adapter {module_name}. Make the parent of Common "
            "importable on the Streamlit host and Airflow workers."
        ) from None
    credentials = adapter._load_json("DB_credentials.json")
    if key not in credentials:
        raise ValueError(f"DB connection key {key!r} is missing from DB_credentials.json")
    return adapter._create_db_engine_analyst_MSSQL(credentials[key])


class MSSQLStorage:
    def __init__(self, *, engine=None):
        self._engine = engine

    @contextmanager
    def connection(self):
        # Create inside the worker/task, never at DAG import time. Dispose only
        # engines owned by this operation; injected engines belong to the caller.
        engine = self._engine if self._engine is not None else create_storage_engine()
        try:
            with engine.begin() as connection:
                yield connection
        finally:
            if self._engine is None:
                engine.dispose()

    @staticmethod
    def lock_write(connection, resource):
        """Serialize competing brief writers until commit/rollback (30s timeout)."""
        if connection.dialect.name == "mssql":
            connection.execute(text("""
                DECLARE @result int;
                EXEC @result = sys.sp_getapplock
                    @Resource=:resource, @LockMode='Exclusive',
                    @LockOwner='Transaction', @LockTimeout=30000;
                IF @result < 0 THROW 51000, 'Brief storage write lock timed out or failed', 1;
            """), {"resource": resource})
