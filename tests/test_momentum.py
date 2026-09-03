import numpy as np
import pandas as pd
import pytest

from processing.momentum import (
    calculate_momentum, compare_sector_distribution, download_security_metadata,
    merge_universes,
)


def synthetic_prices() -> pd.DataFrame:
    steps = np.arange(253, dtype=float)
    dates = pd.bdate_range("2025-08-01", periods=len(steps))
    return pd.DataFrame({
        "FAST": 100 * np.exp(0.0040 * steps + 0.012 * np.sin(steps / 3)),
        "SLOW": 100 * np.exp(0.0012 * steps + 0.020 * np.sin(steps / 4)),
        "DOWN": 100 * np.exp(-0.0010 * steps + 0.018 * np.sin(steps / 5)),
        "FLAT": np.full(len(steps), 100.0),
        "SHORT": np.r_[np.full(30, np.nan), np.linspace(100, 130, len(steps) - 30)],
    }, index=dates)


def test_universes_are_normalized_merged_and_deduplicated() -> None:
    assert merge_universes(["AAPL", "BRK.B", "AAPL"], ["MSFT", "BRK-B"]) == [
        "AAPL", "BRK-B", "MSFT",
    ]


def test_momentum_formula_and_descending_ranking() -> None:
    prices = synthetic_prices()
    ranking = calculate_momentum(prices)
    assert ranking["ticker"].tolist() == ["FAST", "SLOW", "DOWN"]
    assert ranking["rank"].tolist() == [1, 2, 3]
    assert ranking["momentum_score"].is_monotonic_decreasing

    valid = prices[["FAST", "SLOW", "DOWN"]]
    returns_6m = valid.iloc[-1] / valid.iloc[-127] - 1
    returns_12m = valid.iloc[-1] / valid.iloc[-253] - 1
    daily = valid.pct_change(fill_method=None)
    vol_6m = daily.tail(126).std() * np.sqrt(252)
    vol_12m = daily.tail(252).std() * np.sqrt(252)
    expected_fast = (
        (returns_6m["FAST"] - returns_6m.mean()) / vol_6m["FAST"]
        + (returns_12m["FAST"] - returns_12m.mean()) / vol_12m["FAST"]
    ) / 2
    actual_fast = ranking.loc[ranking["ticker"] == "FAST", "momentum_score"].iloc[0]
    assert actual_fast == pytest.approx(expected_fast)


def test_insufficient_history_and_zero_volatility_are_excluded() -> None:
    ranking = calculate_momentum(synthetic_prices())
    assert "SHORT" not in set(ranking["ticker"])
    assert "FLAT" not in set(ranking["ticker"])


def test_yahoo_sector_and_industry_metadata_is_normalized_and_fault_tolerant() -> None:
    def info(ticker: str) -> dict:
        if ticker == "BAD":
            raise RuntimeError("unavailable")
        return {"sector": "Technology", "industry": "Semiconductors"}

    rows = download_security_metadata(["NVDA", "BAD"], info_getter=info, max_workers=2)
    assert rows == [
        {"ticker": "BAD", "sector": "Unknown", "industry": "Unknown"},
        {"ticker": "NVDA", "sector": "Technology", "industry": "Semiconductors"},
    ]


def test_sector_distribution_is_100_percent_for_current_and_previous_top_25() -> None:
    tickers = [f"T{index:02d}" for index in range(25)]
    current = pd.DataFrame({
        "ticker": tickers, "rank": range(1, 26),
        "sector": ["Technology"] * 10 + ["Healthcare"] * 15,
    })
    previous = pd.DataFrame({
        "ticker": tickers, "rank": range(1, 26),
        "sector": ["Technology"] * 5 + ["Healthcare"] * 20,
    })
    three_month = pd.DataFrame({
        "ticker": tickers, "rank": range(1, 26),
        "sector": ["Technology"] * 15 + ["Healthcare"] * 10,
    })
    rows = compare_sector_distribution(current, previous, three_month)
    assert sum(float(row["current_pct"]) for row in rows) == pytest.approx(100.0)
    assert sum(float(row["previous_pct"]) for row in rows) == pytest.approx(100.0)
    assert sum(float(row["three_month_pct"]) for row in rows) == pytest.approx(100.0)
    technology = next(row for row in rows if row["sector"] == "Technology")
    assert technology["current_pct"] == pytest.approx(40.0)
    assert technology["previous_pct"] == pytest.approx(20.0)
    assert technology["change_pp"] == pytest.approx(20.0)
    assert technology["three_month_pct"] == pytest.approx(60.0)
    assert technology["change_3m_pp"] == pytest.approx(-20.0)
