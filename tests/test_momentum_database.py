from datetime import date
from pathlib import Path

import pandas as pd

from storage.momentum_database import (
    MomentumDatabase, build_demo_previous_ranking, calendar_month_before,
    calendar_months_before, compare_rankings,
)


def ranking(tickers: list[str], scores: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": tickers,
        "rank": range(1, len(tickers) + 1),
        "return_6m": [0.2] * len(tickers),
        "return_12m": [0.3] * len(tickers),
        "vol_6m": [0.25] * len(tickers),
        "vol_12m": [0.30] * len(tickers),
        "score_6m": scores,
        "score_12m": scores,
        "momentum_score": scores,
    })


def test_database_initialization_and_idempotent_upsert(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "momentum.db"
    db = MomentumDatabase(path)
    first = ranking(["AAA", "BBB", "CCC"], [3.0, 2.0, 1.0])
    assert path.exists()
    assert db.upsert_rankings(date(2026, 8, 24), first) == 3
    updated = ranking(["BBB", "AAA"], [4.0, 3.0])
    assert db.upsert_rankings(date(2026, 8, 24), updated) == 2
    stored = db.get_ranking_for_date("2026-08-24")
    assert stored["ticker"].tolist() == ["BBB", "AAA"]
    assert len(stored) == 2
    assert stored.iloc[0]["momentum_score"] == 4.0


def test_top_n_and_latest_on_or_before_handle_missing_dates(tmp_path: Path) -> None:
    db = MomentumDatabase(tmp_path / "momentum.db")
    db.upsert_rankings("2026-08-21", ranking(["FRI1", "FRI2", "FRI3"], [3, 2, 1]))
    db.upsert_rankings("2026-08-24", ranking(["MON1", "MON2", "MON3"], [3, 2, 1]))
    assert db.get_top_n_for_date("2026-08-24", 2)["ticker"].tolist() == ["MON1", "MON2"]
    previous = db.get_latest_ranking_on_or_before("2026-08-23")
    assert previous["as_of_date"].unique().tolist() == ["2026-08-21"]
    assert previous["ticker"].tolist() == ["FRI1", "FRI2", "FRI3"]


def test_compare_rankings_prepares_future_monthly_changes() -> None:
    previous = ranking(["AAA", "BBB", "CCC"], [3, 2, 1])
    current = ranking(["BBB", "DDD", "AAA"], [3, 2, 1])
    changes = compare_rankings(current, previous, n=3)
    assert changes["entered"] == ["DDD"]
    assert changes["exited"] == ["CCC"]
    assert changes["remained"] == ["BBB", "AAA"]
    assert changes["rank_changes"][0] == {
        "ticker": "BBB", "previous_rank": 2, "current_rank": 1, "change": 1,
    }


def test_demo_previous_ranking_exercises_entries_exits_without_persistence() -> None:
    current = ranking([f"T{index:02d}" for index in range(1, 13)], list(range(12, 0, -1)))
    demo_previous = build_demo_previous_ranking(current, n=10)
    changes = compare_rankings(current, demo_previous, n=10)
    assert changes["entered"] == ["T09", "T10"]
    assert changes["exited"] == ["T11", "T12"]
    assert any(row["change"] for row in changes["rank_changes"])


def test_calendar_month_before_clamps_end_of_month() -> None:
    assert calendar_month_before("2026-03-31") == date(2026, 2, 28)
    assert calendar_month_before("2026-01-15") == date(2025, 12, 15)
    assert calendar_months_before("2026-05-31", 3) == date(2026, 2, 28)


def test_security_metadata_is_cached_and_ranking_snapshot_keeps_classification(tmp_path: Path) -> None:
    db = MomentumDatabase(tmp_path / "momentum.db")
    assert db.metadata_tickers_to_refresh(["NVDA"]) == ["NVDA"]
    assert db.upsert_security_metadata([{
        "ticker": "NVDA", "sector": "Technology", "industry": "Semiconductors",
    }]) == 1
    assert db.metadata_tickers_to_refresh(["NVDA"]) == []
    metadata = db.get_security_metadata(["NVDA"])
    assert metadata["NVDA"]["industry"] == "Semiconductors"

    frame = ranking(["NVDA"], [3.0])
    frame["sector"] = "Technology"
    frame["industry"] = "Semiconductors"
    db.upsert_rankings("2026-08-26", frame)
    stored = db.get_ranking_for_date("2026-08-26")
    assert stored.iloc[0]["sector"] == "Technology"
    assert stored.iloc[0]["industry"] == "Semiconductors"
