from unittest.mock import Mock

import pytest

from storage.momentum_database import MomentumDatabase
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError, OperationalError
from storage import mssql
from storage.mssql import rankings
from tests.test_momentum_database import ranking


def test_constructor_does_not_connect_or_create_tables():
    engine = Mock()
    db = MomentumDatabase(engine=engine)
    assert db is not None
    assert engine.mock_calls == []


def test_old_sqlite_path_is_not_silently_used(tmp_path):
    with pytest.raises(TypeError):
        MomentumDatabase(tmp_path / "old.db")


def test_failed_snapshot_rolls_back_deletions_and_preserves_old_day(storage_engine):
    db = MomentumDatabase(engine=storage_engine)
    db.upsert_rankings("2026-10-01", ranking(["OLD"], [1.0]))
    invalid = ranking(["NEW"], [float("nan")])
    with pytest.raises(IntegrityError):
        db.upsert_rankings("2026-10-01", invalid)
    assert db.get_ranking_for_date("2026-10-01")["ticker"].tolist() == ["OLD"]


def test_rerun_keeps_classification_creation_time_and_other_dates(storage_engine):
    db = MomentumDatabase(engine=storage_engine)
    first = ranking(["A'B"], [1.0])
    first["sector"] = "Technology"
    first["industry"] = "Software"
    db.upsert_rankings("2026-10-01", first)
    with db.connection() as con:
        created_at = con.execute(select(rankings.c.created_at)).scalar_one()
    db.upsert_rankings("2026-10-02", ranking(["NEXT"], [4.0]))
    db.upsert_rankings("2026-10-01", ranking(["A'B"], [2.0]))
    actual = db.get_ranking_for_date("2026-10-01").iloc[0]
    assert actual["sector"] == "Technology"
    assert actual["momentum_score"] == 2.0
    with db.connection() as con:
        assert con.execute(select(rankings.c.created_at).where(
            rankings.c.ticker == "A'B")).scalar_one() == created_at
    assert db.get_ranking_for_date("2026-10-02")["ticker"].tolist() == ["NEXT"]


def test_metadata_large_list_refresh_and_duplicate_ticker(storage_engine):
    db = MomentumDatabase(engine=storage_engine)
    rows = [{"ticker": f"T{i}", "sector": "Energy"} for i in range(2200)]
    assert db.upsert_security_metadata(rows) == 2200
    assert len(db.get_security_metadata([r["ticker"] for r in rows])) == 2200
    db.upsert_security_metadata([{"ticker": "T0", "sector": "Old"},
                                 {"ticker": "T0", "sector": "Healthcare"}])
    assert db.get_security_metadata(["T0"])["T0"]["sector"] == "Healthcare"
    assert db.metadata_tickers_to_refresh(["T0", "MISSING"]) == ["MISSING"]


def test_missing_tables_are_not_created_automatically():
    engine = create_engine("sqlite://", execution_options={"schema_translate_map": {"dbo": None}})
    try:
        with pytest.raises(OperationalError):
            MomentumDatabase(engine=engine).get_ranking_for_date("2026-10-01")
    finally:
        engine.dispose()


def test_adapter_uses_selected_analyst_credentials(monkeypatch):
    class Adapter:
        @staticmethod
        def _load_json(filename):
            assert filename == "DB_credentials.json"
            return {"Analyst_TEST2_Airflow": {"selected": True}, "OTHER": {}}

        @staticmethod
        def _create_db_engine_analyst_MSSQL(credentials):
            assert credentials == {"selected": True}
            return "selected-engine"

    def import_adapter(name):
        assert name == "Common.DB_adapter.DB_request"
        return Adapter

    monkeypatch.delenv("BRIEF_DB_CONNECTION", raising=False)
    monkeypatch.delenv("BRIEF_DB_ADAPTER_MODULE", raising=False)
    monkeypatch.setattr(mssql, "import_module", import_adapter)
    assert mssql.create_storage_engine() == "selected-engine"


def test_storage_statements_compile_for_sql_server(storage_engine):
    from sqlalchemy.dialects.mssql.pymssql import MSDialect_pymssql

    def compile_statement(conn, cursor, statement, parameters, context, executemany):
        if context.compiled is not None:
            context.compiled.statement.compile(dialect=MSDialect_pymssql())

    event.listen(storage_engine, "before_cursor_execute", compile_statement)
    db = MomentumDatabase(engine=storage_engine)
    db.upsert_rankings("2026-10-01", ranking(["A", "B"], [2.0, 1.0]))
    db.upsert_rankings("2026-10-01", ranking(["B"], [3.0]))
    assert db.get_latest_ranking_on_or_before("2026-10-02")["ticker"].tolist() == ["B"]


def test_case_distinct_identifiers_remain_separate(storage_engine):
    db = MomentumDatabase(engine=storage_engine)
    db.upsert_rankings("2026-10-01", ranking(["ABC", "abc"], [2.0, 1.0]))
    db.upsert_security_metadata([{"ticker": "ABC", "sector": "Energy"},
                                 {"ticker": "abc", "sector": "Technology"}])
    assert db.get_ranking_for_date("2026-10-01")["ticker"].tolist() == ["ABC", "abc"]
    assert db.get_security_metadata(["ABC", "abc"])["abc"]["sector"] == "Technology"
