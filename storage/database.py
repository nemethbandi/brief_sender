from datetime import timezone

from sqlalchemy import insert

from storage.mssql import MSSQLStorage, sent_reports
from utils.helpers import utc_now


class Database(MSSQLStorage):
    def record_report(self, recipient: str, subject: str, article_count: int, mode: str) -> None:
        with self.connection() as con:
            con.execute(insert(sent_reports), {
                "timestamp": utc_now().astimezone(timezone.utc).replace(tzinfo=None),
                "recipient": recipient, "subject": subject,
                "article_count": article_count, "mode": mode,
            })
