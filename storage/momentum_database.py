from __future__ import annotations

import os
import sqlite3
from calendar import monthrange
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

from config.settings import BASE_DIR
from processing.momentum import RANKING_COLUMNS
from utils.logger import get_logger

logger = get_logger(__name__)

MOMENTUM_DB_PATH = Path(os.getenv(
    "MOMENTUM_DB_PATH", str(BASE_DIR / "storage" / "momentum.db"),
))


def _date_text(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def calendar_month_before(value: date | datetime | str) -> date:
    return calendar_months_before(value, 1)


def calendar_months_before(value: date | datetime | str, months: int) -> date:
    if months < 1:
        raise ValueError("months must be at least 1")
    current = date.fromisoformat(_date_text(value))
    absolute_month = current.year * 12 + current.month - 1 - months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    return date(year, month, min(current.day, monthrange(year, month)[1]))


class MomentumDatabase:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else MOMENTUM_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS momentum_rankings (
                    as_of_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    return_6m REAL,
                    return_12m REAL,
                    vol_6m REAL,
                    vol_12m REAL,
                    score_6m REAL,
                    score_12m REAL,
                    momentum_score REAL NOT NULL,
                    sector TEXT,
                    industry TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (as_of_date, ticker)
                )
            """)
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_momentum_date_rank "
                "ON momentum_rankings(as_of_date, rank)"
            )
            columns = {row["name"] for row in con.execute(
                "PRAGMA table_info(momentum_rankings)"
            ).fetchall()}
            if "sector" not in columns:
                con.execute("ALTER TABLE momentum_rankings ADD COLUMN sector TEXT")
            if "industry" not in columns:
                con.execute("ALTER TABLE momentum_rankings ADD COLUMN industry TEXT")
            con.execute("""
                CREATE TABLE IF NOT EXISTS security_metadata (
                    ticker TEXT PRIMARY KEY,
                    sector TEXT NOT NULL DEFAULT 'Unknown',
                    industry TEXT NOT NULL DEFAULT 'Unknown',
                    updated_at TEXT NOT NULL
                )
            """)

    def upsert_rankings(
        self, as_of_date: date | datetime | str, ranking: pd.DataFrame,
    ) -> int:
        if ranking is None or ranking.empty:
            logger.warning("No momentum rows to persist for %s", _date_text(as_of_date))
            return 0
        missing = set(RANKING_COLUMNS) - set(ranking.columns)
        if missing:
            raise ValueError(f"Momentum ranking is missing columns: {sorted(missing)}")
        day = _date_text(as_of_date)
        def optional_text(value: object) -> str | None:
            return None if value is None or pd.isna(value) else str(value)

        rows = [(
            day, str(row["ticker"]), int(row["rank"]), float(row["return_6m"]),
            float(row["return_12m"]), float(row["vol_6m"]), float(row["vol_12m"]),
            float(row["score_6m"]), float(row["score_12m"]), float(row["momentum_score"]),
            optional_text(row.get("sector")), optional_text(row.get("industry")),
        ) for _, row in ranking.iterrows()]
        with self.connection() as con:
            con.executemany("""
                INSERT INTO momentum_rankings(
                    as_of_date,ticker,rank,return_6m,return_12m,vol_6m,vol_12m,
                    score_6m,score_12m,momentum_score,sector,industry
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(as_of_date,ticker) DO UPDATE SET
                    rank=excluded.rank,
                    return_6m=excluded.return_6m,
                    return_12m=excluded.return_12m,
                    vol_6m=excluded.vol_6m,
                    vol_12m=excluded.vol_12m,
                    score_6m=excluded.score_6m,
                    score_12m=excluded.score_12m,
                    momentum_score=excluded.momentum_score,
                    sector=COALESCE(excluded.sector,momentum_rankings.sector),
                    industry=COALESCE(excluded.industry,momentum_rankings.industry)
            """, rows)
            placeholders = ",".join("?" for _ in rows)
            con.execute(
                f"DELETE FROM momentum_rankings WHERE as_of_date=? "
                f"AND ticker NOT IN ({placeholders})",
                (day, *(row[1] for row in rows)),
            )
        logger.info("Upserted %d momentum ranking rows for %s", len(rows), day)
        return len(rows)

    def get_ranking_for_date(
        self, as_of_date: date | datetime | str,
    ) -> pd.DataFrame:
        day = _date_text(as_of_date)
        with self.connection() as con:
            rows = con.execute(
                "SELECT as_of_date,ticker,rank,return_6m,return_12m,vol_6m,vol_12m,"
                "score_6m,score_12m,momentum_score,sector,industry FROM momentum_rankings "
                "WHERE as_of_date=? ORDER BY rank", (day,),
            ).fetchall()
        return pd.DataFrame(
            [dict(row) for row in rows],
            columns=["as_of_date", *RANKING_COLUMNS, "sector", "industry"],
        )

    def get_top_n_for_date(
        self, as_of_date: date | datetime | str, n: int = 25,
    ) -> pd.DataFrame:
        ranking = self.get_ranking_for_date(as_of_date)
        return ranking.loc[ranking["rank"] <= int(n)].reset_index(drop=True)

    def get_latest_ranking_on_or_before(
        self, target_date: date | datetime | str,
    ) -> pd.DataFrame:
        target = _date_text(target_date)
        with self.connection() as con:
            row = con.execute(
                "SELECT MAX(as_of_date) AS as_of_date FROM momentum_rankings "
                "WHERE as_of_date<=?", (target,),
            ).fetchone()
        if row is None or row["as_of_date"] is None:
            return pd.DataFrame(columns=["as_of_date", *RANKING_COLUMNS, "sector", "industry"])
        return self.get_ranking_for_date(row["as_of_date"])

    def get_security_metadata(self, tickers: list[str]) -> dict[str, dict[str, str]]:
        symbols = sorted(set(tickers))
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        with self.connection() as con:
            rows = con.execute(
                f"SELECT ticker,sector,industry,updated_at FROM security_metadata "
                f"WHERE ticker IN ({placeholders})", symbols,
            ).fetchall()
        return {row["ticker"]: dict(row) for row in rows}

    def metadata_tickers_to_refresh(
        self, tickers: list[str], max_age_days: int = 30,
    ) -> list[str]:
        metadata = self.get_security_metadata(tickers)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        result: list[str] = []
        for ticker in sorted(set(tickers)):
            row = metadata.get(ticker)
            if row is None:
                result.append(ticker)
                continue
            try:
                updated = datetime.fromisoformat(row["updated_at"])
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                if updated < cutoff:
                    result.append(ticker)
            except (TypeError, ValueError):
                result.append(ticker)
        return result

    def upsert_security_metadata(self, rows: list[dict[str, str]]) -> int:
        if not rows:
            return 0
        updated_at = datetime.now(timezone.utc).isoformat()
        values = [(
            str(row["ticker"]), str(row.get("sector") or "Unknown"),
            str(row.get("industry") or "Unknown"), updated_at,
        ) for row in rows]
        with self.connection() as con:
            con.executemany("""
                INSERT INTO security_metadata(ticker,sector,industry,updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(ticker) DO UPDATE SET
                    sector=excluded.sector,
                    industry=excluded.industry,
                    updated_at=excluded.updated_at
            """, values)
        logger.info("Upserted %d Yahoo sector/industry metadata rows", len(values))
        return len(values)


def compare_rankings(
    current_df: pd.DataFrame, previous_df: pd.DataFrame, n: int = 25,
) -> dict[str, list]:
    """Prepare Top-N membership/rank changes for a future email section."""
    current = current_df.sort_values("rank").head(n).set_index("ticker")
    previous = previous_df.sort_values("rank").head(n).set_index("ticker")
    current_tickers = set(current.index)
    previous_tickers = set(previous.index)
    entered = sorted(current_tickers - previous_tickers, key=lambda t: current.loc[t, "rank"])
    exited = sorted(previous_tickers - current_tickers, key=lambda t: previous.loc[t, "rank"])
    remained = sorted(current_tickers & previous_tickers, key=lambda t: current.loc[t, "rank"])
    rank_changes = [{
        "ticker": ticker,
        "previous_rank": int(previous.loc[ticker, "rank"]),
        "current_rank": int(current.loc[ticker, "rank"]),
        "change": int(previous.loc[ticker, "rank"] - current.loc[ticker, "rank"]),
    } for ticker in remained]
    return {
        "entered": entered,
        "exited": exited,
        "remained": remained,
        "rank_changes": rank_changes,
    }


def build_demo_previous_ranking(
    current_df: pd.DataFrame, n: int = 25, replacement_count: int = 2,
) -> pd.DataFrame:
    """Create an in-memory prior Top-N for UI/email testing; never persist it."""
    ordered = current_df.sort_values("rank").reset_index(drop=True)
    replacement_count = max(1, min(replacement_count, n - 1))
    if len(ordered) < n + replacement_count:
        return ordered.head(n).copy()
    retained = ordered.head(n - replacement_count).copy()
    reserves = ordered.iloc[n:n + replacement_count].copy()
    demo = pd.concat([retained, reserves], ignore_index=True)
    # Swap adjacent retained names so the preview also demonstrates rank changes.
    order = list(range(len(demo)))
    for index in range(0, min(n - replacement_count, len(order) - 1), 2):
        order[index], order[index + 1] = order[index + 1], order[index]
    demo = demo.iloc[order].reset_index(drop=True)
    demo["rank"] = range(1, len(demo) + 1)
    return demo
