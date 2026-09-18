import sqlite3

from storage.database import Database


def test_email_audit_preserves_existing_database(tmp_path):
    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE portfolio (ticker TEXT)")
        con.execute("INSERT INTO portfolio VALUES ('OLD')")
    db = Database(path)
    db.record_report("test@example.com", "Brief", 0, "opened")
    with db.connection() as con:
        assert con.execute("SELECT ticker FROM portfolio").fetchone()[0] == "OLD"
        assert con.execute("SELECT recipient, mode FROM sent_reports").fetchone()[:] == ("test@example.com", "opened")
