from datetime import date

import pandas as pd
import pytest

from processing import momentum
from storage.momentum_database import MomentumDatabase
from workflows.daily_brief import build_momentum_context
from reports.report_builder import ReportBuilder


def frame(sectors):
    return pd.DataFrame({"ticker": [f"T{i}" for i in range(len(sectors))],
                         "rank": range(1, len(sectors) + 1), "sector": sectors})


def test_default_sector_sample_is_top100_in_each_period():
    rows = momentum.compare_sector_distribution(
        frame(["Technology"] * 40 + ["Energy"] * 60 + ["Utilities"] * 400),
        frame(["Technology"] * 30 + ["Energy"] * 70 + ["Utilities"] * 400),
        frame(["Technology"] * 20 + ["Energy"] * 80 + ["Utilities"] * 400),
    )
    assert sum(r["current_count"] for r in rows) == 100
    assert {r["sector"] for r in rows} == {"Technology", "Energy"}
    tech = next(r for r in rows if r["sector"] == "Technology")
    assert (tech["current_pct"], tech["previous_pct"], tech["three_month_pct"]) == (40, 30, 20)
    assert (tech["change_pp"], tech["change_3m_pp"]) == (10, 20)


def test_sector_aliases_are_grouped_and_unknown_names_are_colored():
    rows = momentum.compare_sector_distribution(
        frame([" Technology ", "technology", "Information Technology", "Financials",
               "Consumer Non-Cyclicals", "Health Care", "Custom Sector", None]),
        frame(["Technology", "Financial Services"]),
    )
    by_sector = {r["sector"]: r for r in rows}
    assert by_sector["Technology"]["current_count"] == 3
    assert by_sector["Financial Services"]["current_count"] == 1
    assert by_sector["Healthcare"]["current_count"] == 1
    assert by_sector["Consumer Defensive"]["current_count"] == 1
    gray = {"#596579", "#9aa8a0"}
    assert by_sector["Custom Sector"]["color"] not in gray
    assert by_sector["Unknown"]["color"] == "#9aa8a0"
    other = momentum.compare_sector_distribution(frame(["Custom Sector"]), frame([]))
    assert other[0]["color"] == by_sector["Custom Sector"]["color"]
    assert sum(r["current_pct"] for r in rows) == pytest.approx(100)


def test_workflow_enriches_top100_and_repairs_unknown_historical_sectors(monkeypatch, tmp_path):
    ranking = frame(["Unknown"] * 110)
    for column in momentum.RANKING_COLUMNS:
        if column not in ranking:
            ranking[column] = 1.0
    ranking = ranking.drop(columns="sector")
    database = MomentumDatabase(tmp_path / "momentum.db")
    old = ranking.copy()
    old["sector"] = "Unknown"
    database.upsert_rankings("2026-08-18", old)
    database.upsert_rankings("2026-06-18", old)
    requested = []

    class Provider:
        def get_momentum_universe(self):
            return ranking["ticker"].tolist()

        def get_momentum_prices(self, tickers):
            return pd.DataFrame()

        def get_security_metadata(self, tickers):
            requested.extend(tickers)
            return [{"ticker": t, "sector": "Financials", "industry": "Banking"} for t in tickers]

    monkeypatch.setattr(momentum, "calculate_momentum", lambda prices: ranking.copy())
    result = build_momentum_context(date(2026, 9, 18), Provider(), database)
    assert set(requested) == set(ranking.head(100)["ticker"])
    row = result["sector_comparison"][0]
    assert row["sector"] == "Financial Services"
    assert (row["current_count"], row["previous_count"], row["three_month_count"]) == (100, 100, 100)
    assert len(result["changes"]["remained"]) == 25


def test_email_renders_top100_colors_for_all_periods():
    rows = momentum.compare_sector_distribution(
        frame(["Information Technology"] * 40 + ["Financials"] * 60),
        frame(["Technology"] * 30 + ["Financial Services"] * 70),
        frame(["technology"] * 20 + ["financials"] * 80),
    )
    report = ReportBuilder().build(
        "Brief", [], momentum_sector_comparison=rows,
        momentum_comparison_date="2026-08-18", momentum_sector_3m_date="2026-06-18",
    )
    assert "MOMENTUM TOP 100 SECTOR DISTRIBUTION" in report.html
    for sector in ("Technology", "Financial Services"):
        assert report.html.count(f'bgcolor="{momentum.SECTOR_COLORS[sector]}"') == 3
    assert "Technology 40.0%" in report.html
    assert "Technology 30.0%" in report.html
    assert "Technology 20.0%" in report.html
