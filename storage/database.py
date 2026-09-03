from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config.settings import BASE_DIR


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else BASE_DIR / "storage" / "market_brief.db"
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
        with self.connection() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS portfolio (
                    ticker TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
                    keywords TEXT NOT NULL DEFAULT '[]', enabled INTEGER NOT NULL DEFAULT 1,
                    threshold_pct REAL NOT NULL DEFAULT 2.0);
                CREATE TABLE IF NOT EXISTS assets (
                    ticker TEXT PRIMARY KEY, name TEXT NOT NULL, asset_class TEXT NOT NULL,
                    region TEXT NOT NULL, format TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS news_articles (
                    url_hash TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
                    publisher TEXT, published_at TEXT, score REAL, first_seen TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sent_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                    recipient TEXT NOT NULL, subject TEXT NOT NULL, article_count INTEGER NOT NULL,
                    mode TEXT NOT NULL);
            """)
            columns = {row["name"] for row in con.execute("PRAGMA table_info(portfolio)").fetchall()}
            if "threshold_pct" not in columns:
                con.execute("ALTER TABLE portfolio ADD COLUMN threshold_pct REAL NOT NULL DEFAULT 2.0")

    def seed_json(self, portfolio_path: Path, assets_path: Path) -> None:
        configured_assets = json.loads(assets_path.read_text(encoding="utf-8"))
        with self.connection() as con:
            if con.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0] == 0:
                for item in json.loads(portfolio_path.read_text(encoding="utf-8")):
                    con.execute("INSERT INTO portfolio(ticker,name,keywords,enabled,threshold_pct) VALUES (?,?,?,?,?)", (
                        item["ticker"], item.get("name", ""), json.dumps(item.get("keywords", [])),
                        int(item.get("enabled", True)), float(item.get("threshold_pct", 2.0))))
            if con.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0:
                for item in configured_assets:
                    con.execute("INSERT INTO assets VALUES (?,?,?,?,?,?)", (
                        item["ticker"], item["name"], item["asset_class"], item.get("region", "Other"),
                        item.get("format", "number"), int(item.get("enabled", True))))
            self._migrate_us_futures_layout(con, configured_assets)
            self._add_required_asset(con, configured_assets, "SI=F")

    @staticmethod
    def _add_required_asset(con: sqlite3.Connection, configured_assets: list[dict[str, Any]], ticker: str) -> None:
        item = next((asset for asset in configured_assets if asset["ticker"] == ticker), None)
        if item:
            con.execute(
                "INSERT OR IGNORE INTO assets VALUES (?,?,?,?,?,?)",
                (item["ticker"], item["name"], item["asset_class"], item.get("region", "Other"),
                 item.get("format", "number"), int(item.get("enabled", True))),
            )

    @staticmethod
    def _migrate_us_futures_layout(con: sqlite3.Connection, configured_assets: list[dict[str, Any]]) -> None:
        """Add the futures-first layout without replacing user-edited assets."""
        futures = {item["ticker"]: item for item in configured_assets if item.get("region") == "US Futures"}
        for item in futures.values():
            con.execute(
                "INSERT OR IGNORE INTO assets VALUES (?,?,?,?,?,?)",
                (item["ticker"], item["name"], item["asset_class"], item["region"],
                 item.get("format", "number"), int(item.get("enabled", True))),
            )
        cash_defaults = {
            "^GSPC": ("S&P 500", "S&P 500 Cash"),
            "^NDX": ("Nasdaq 100", "Nasdaq 100 Cash"),
            "^DJI": ("Dow Jones", "Dow Jones Cash"),
            "^RUT": ("Russell 2000", "Russell 2000 Cash"),
        }
        for ticker, (old_name, new_name) in cash_defaults.items():
            con.execute(
                "UPDATE assets SET name=?, region='Previous US Close' WHERE ticker=? AND name=?",
                (new_name, ticker, old_name),
            )

    def list_portfolio(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM portfolio" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY ticker"
        with self.connection() as con:
            rows = con.execute(query).fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"]), "keywords": json.loads(row["keywords"]),
                 "threshold_pct": float(row["threshold_pct"])} for row in rows]

    def replace_portfolio(self, rows: list[dict[str, Any]]) -> None:
        with self.connection() as con:
            con.execute("DELETE FROM portfolio")
            con.executemany("INSERT INTO portfolio(ticker,name,keywords,enabled,threshold_pct) VALUES (?,?,?,?,?)", [(
                row["ticker"], row.get("name", ""), json.dumps(row.get("keywords") or [row["ticker"], row.get("name", "")]),
                int(row.get("enabled", True)), float(row.get("threshold_pct", 2.0))) for row in rows])

    def list_assets(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM assets" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY CASE region " \
            "WHEN 'US Futures' THEN 1 WHEN 'Previous US Close' THEN 2 WHEN 'Europe' THEN 3 WHEN 'Asia' THEN 4 " \
            "WHEN 'Volatility' THEN 5 WHEN 'Commodities' THEN 6 WHEN 'FX' THEN 7 WHEN 'Rates' THEN 8 ELSE 9 END, " \
            "CASE ticker WHEN 'ES=F' THEN 1 WHEN '^GSPC' THEN 1 WHEN 'NQ=F' THEN 2 WHEN '^NDX' THEN 2 " \
            "WHEN 'YM=F' THEN 3 WHEN '^DJI' THEN 3 WHEN 'RTY=F' THEN 4 WHEN '^RUT' THEN 4 ELSE 5 END, name"
        with self.connection() as con:
            rows = con.execute(query).fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]

    def replace_assets(self, rows: list[dict[str, Any]]) -> None:
        with self.connection() as con:
            con.execute("DELETE FROM assets")
            con.executemany("INSERT INTO assets VALUES (?,?,?,?,?,?)", [(
                row["ticker"], row["name"], row["asset_class"], row.get("region", "Other"),
                row.get("format", "number"), int(row.get("enabled", True))) for row in rows])

    def store_articles(self, articles: list[Any]) -> None:
        import hashlib
        with self.connection() as con:
            for item in articles:
                digest = hashlib.sha256(item.url.encode("utf-8")).hexdigest()
                con.execute("INSERT OR IGNORE INTO news_articles(url_hash,url,title,publisher,published_at,score) VALUES(?,?,?,?,?,?)",
                            (digest, item.url, item.title, item.publisher, item.published_at.isoformat(), item.score))

    def record_report(self, recipient: str, subject: str, article_count: int, mode: str) -> None:
        from utils.helpers import utc_now
        with self.connection() as con:
            con.execute("INSERT INTO sent_reports(timestamp,recipient,subject,article_count,mode) VALUES(?,?,?,?,?)",
                        (utc_now().isoformat(), recipient, subject, article_count, mode))
