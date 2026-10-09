from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import bindparam, delete, func, insert, select, update

from processing.momentum import RANKING_COLUMNS
from storage.mssql import MSSQLStorage, rankings, security_metadata
from utils.logger import get_logger

logger = get_logger(__name__)


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


class MomentumDatabase(MSSQLStorage):
    def upsert_rankings(self, as_of_date, ranking: pd.DataFrame) -> int:
        if ranking is None or ranking.empty:
            logger.warning("No momentum rows to persist for %s", _date_text(as_of_date))
            return 0
        missing = set(RANKING_COLUMNS) - set(ranking.columns)
        if missing:
            raise ValueError(f"Momentum ranking is missing columns: {sorted(missing)}")
        if ranking["ticker"].isna().any() or ranking["ticker"].astype(str).duplicated().any():
            raise ValueError("Momentum tickers must be non-null and unique")
        day = date.fromisoformat(_date_text(as_of_date))
        rows = []
        for _, row in ranking.iterrows():
            record = {"as_of_date": day, "ticker": str(row["ticker"]), "rank": int(row["rank"])}
            for field in RANKING_COLUMNS:
                if field not in ("ticker", "rank"):
                    record[field] = None if pd.isna(row[field]) else float(row[field])
            for field in ("sector", "industry"):
                value = row.get(field)
                record[field] = None if value is None or pd.isna(value) else str(value)
            rows.append(record)
        with self.connection() as con:
            self.lock_write(con, f"brief:dbo.momentum_rankings:{day.isoformat()}")
            existing = set(con.execute(select(rankings.c.ticker).where(
                rankings.c.as_of_date == day,
            )).scalars())
            incoming = {row["ticker"] for row in rows}
            stale = existing - incoming
            if stale:
                con.execute(delete(rankings).where(
                    rankings.c.as_of_date == day,
                    rankings.c.ticker == bindparam("key_ticker"),
                ), [{"key_ticker": ticker} for ticker in sorted(stale)])
            new = [row for row in rows if row["ticker"] not in existing]
            changed = [row for row in rows if row["ticker"] in existing]
            if new:
                con.execute(insert(rankings), new)
            if changed:
                fields = [*RANKING_COLUMNS, "sector", "industry"]
                values = {field: bindparam("value_" + field) for field in fields if field != "ticker"}
                for field in ("sector", "industry"):
                    values[field] = func.coalesce(bindparam("value_" + field), rankings.c[field])
                con.execute(update(rankings).where(
                    rankings.c.as_of_date == day,
                    rankings.c.ticker == bindparam("key_ticker"),
                ).values(**values), [
                    {"key_ticker": row["ticker"], **{
                        "value_" + field: row[field] for field in fields if field != "ticker"
                    }} for row in changed
                ])
        logger.info("Upserted %d momentum ranking rows for %s", len(rows), day)
        return len(rows)

    def get_ranking_for_date(self, as_of_date) -> pd.DataFrame:
        columns = ["as_of_date", *RANKING_COLUMNS, "sector", "industry"]
        with self.connection() as con:
            rows = con.execute(select(*(rankings.c[c] for c in columns)).where(
                rankings.c.as_of_date == date.fromisoformat(_date_text(as_of_date)),
            ).order_by(rankings.c.rank)).mappings().all()
        frame = pd.DataFrame(rows, columns=columns)
        frame["as_of_date"] = frame["as_of_date"].map(_date_text)
        return frame

    def get_top_n_for_date(self, as_of_date, n: int = 25) -> pd.DataFrame:
        ranking = self.get_ranking_for_date(as_of_date)
        return ranking.loc[ranking["rank"] <= int(n)].reset_index(drop=True)

    def get_latest_ranking_on_or_before(self, target_date) -> pd.DataFrame:
        with self.connection() as con:
            day = con.execute(select(func.max(rankings.c.as_of_date)).where(
                rankings.c.as_of_date <= date.fromisoformat(_date_text(target_date)),
            )).scalar_one()
        if day is None:
            return pd.DataFrame(columns=["as_of_date", *RANKING_COLUMNS, "sector", "industry"])
        return self.get_ranking_for_date(day)

    def get_security_metadata(self, tickers: list[str]) -> dict[str, dict[str, str]]:
        symbols = sorted(set(tickers))
        if not symbols:
            return {}
        result = {}
        with self.connection() as con:
            # Keep well below SQL Server's parameter limit, even for large universes.
            for start in range(0, len(symbols), 500):
                rows = con.execute(select(security_metadata).where(
                    security_metadata.c.ticker.in_(symbols[start:start + 500]),
                )).mappings()
                for row in rows:
                    item = dict(row)
                    item["updated_at"] = item["updated_at"].isoformat()
                    result[item["ticker"]] = item
        return result

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
        updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        # Last value wins when the provider repeats a ticker.
        records = {str(row["ticker"]): {
            "ticker": str(row["ticker"]), "sector": str(row.get("sector") or "Unknown"),
            "industry": str(row.get("industry") or "Unknown"), "updated_at": updated_at,
        } for row in rows}
        with self.connection() as con:
            self.lock_write(con, "brief:dbo.security_metadata")
            existing = set()
            symbols = list(records)
            for start in range(0, len(symbols), 500):
                existing.update(con.execute(select(security_metadata.c.ticker).where(
                    security_metadata.c.ticker.in_(symbols[start:start + 500]),
                )).scalars())
            new = [row for ticker, row in records.items() if ticker not in existing]
            changed = [row for ticker, row in records.items() if ticker in existing]
            if new:
                con.execute(insert(security_metadata), new)
            if changed:
                con.execute(update(security_metadata).where(
                    security_metadata.c.ticker == bindparam("key_ticker"),
                ).values(sector=bindparam("value_sector"), industry=bindparam("value_industry"),
                         updated_at=bindparam("value_updated_at")), [{
                    "key_ticker": row["ticker"], "value_sector": row["sector"],
                    "value_industry": row["industry"], "value_updated_at": row["updated_at"],
                } for row in changed])
        logger.info("Upserted %d LSEG sector/industry metadata rows", len(records))
        return len(records)


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
