from pathlib import Path
import sqlite3

from storage.database import Database


def test_portfolio_configuration_round_trip(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.replace_portfolio([{"ticker":"ABC","name":"Alpha Beta","keywords":["ABC","Alpha Beta"],"enabled":True,"threshold_pct":1.3}])
    assert db.list_portfolio(True) == [{"ticker":"ABC","name":"Alpha Beta","keywords":["ABC","Alpha Beta"],"enabled":True,"threshold_pct":1.3}]


def test_existing_portfolio_database_gets_threshold_migration(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE portfolio (ticker TEXT PRIMARY KEY, name TEXT, keywords TEXT, enabled INTEGER)")
        con.execute("INSERT INTO portfolio VALUES ('OLD','Legacy','[]',1)")
    db = Database(path)
    assert db.list_portfolio(True)[0]["threshold_pct"] == 2.0
