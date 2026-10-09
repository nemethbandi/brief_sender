from sqlalchemy import select
from storage.database import Database
from storage.mssql import sent_reports


def test_email_audit_persists_recipient_and_mode(storage_engine):
    db = Database(engine=storage_engine)
    db.record_report("test@example.com", "Brief", 0, "opened")
    with db.connection() as con:
        row = con.execute(select(sent_reports)).mappings().one()
    assert row["recipient"] == "test@example.com"
    assert row["mode"] == "opened"
    assert row["subject"] == "Brief"
