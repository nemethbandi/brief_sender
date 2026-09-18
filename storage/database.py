from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config.settings import BASE_DIR


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        configured = path or os.getenv("MARKET_BRIEF_DB_PATH")
        self.path = Path(configured) if configured else BASE_DIR / "storage" / "market_brief.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        # Keep existing databases intact; only the email audit table is used now.
        with self.connection() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS sent_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                    recipient TEXT NOT NULL, subject TEXT NOT NULL, article_count INTEGER NOT NULL,
                    mode TEXT NOT NULL)
            """)

    def record_report(self, recipient: str, subject: str, article_count: int, mode: str) -> None:
        from utils.helpers import utc_now
        with self.connection() as con:
            con.execute("INSERT INTO sent_reports(timestamp,recipient,subject,article_count,mode) VALUES(?,?,?,?,?)",
                        (utc_now().isoformat(), recipient, subject, article_count, mode))
