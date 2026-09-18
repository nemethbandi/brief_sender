from datetime import datetime, timezone

import pandas as pd

from data.models import MarketQuote
from reports.report_builder import ReportBuilder


def test_report_generation_escapes_content_without_news_section() -> None:
    holding = MarketQuote("<script>alert(1)</script> NVIDIA", "NVDA", "stock", 120, 115, 5, 4.3478,
                          datetime.now(timezone.utc), "currency", threshold_pct=2.5)
    report = ReportBuilder(timezone_name="UTC").build(
        "Brief", [holding], holding.timestamp,
        portfolio_quotes=[holding],
    )
    assert "&lt;script&gt;" in report.html
    assert "<script>alert(1)</script>" not in report.html
    assert "PORTFOLIO STATUS" in report.html
    assert "NOTABLE PORTFOLIO MOVES" in report.html
    assert "TOP &amp; WORST PERFORMERS" in report.html
    assert "PORTFOLIO NEWS" not in report.html
    assert "±2.5%" in report.html
    assert "NVDA" in report.html
    assert "#3a7059" in report.html
    assert "#50b748" in report.html
    assert report.html.index("NOTABLE PORTFOLIO MOVES") < report.html.index("PORTFOLIO STATUS")
    assert report.subject.startswith("Brief |")


def test_report_references_generated_history_image(monkeypatch) -> None:
    monkeypatch.setattr(
        "reports.report_builder.build_performer_history_image",
        lambda histories, quotes, output_dir, generated_at, group_key, color:
            ((f"C:/charts/{group_key}.png", f"{group_key}-cid"), None),
    )
    holding = MarketQuote("Alpha", "AAA", "stock", percentage_change=1.0, threshold_pct=2.0)
    report = ReportBuilder(timezone_name="UTC").build(
        "Brief", [], portfolio_quotes=[holding], portfolio_history={"AAA": object()}
    )
    assert 'src="cid:top_performers-cid"' in report.html
    assert 'src="cid:worst_performers-cid"' in report.html
    assert report.inline_images == [
        ("C:/charts/top_performers.png", "top_performers-cid"),
        ("C:/charts/worst_performers.png", "worst_performers-cid"),
    ]


def test_report_contains_stop_and_watch_lists_from_six_month_peak() -> None:
    dates = pd.to_datetime(["2026-03-01", "2026-06-01", "2026-08-20"])
    stop = MarketQuote("Stop Co", "STOP", "stock", current_price=80.0)
    watch = MarketQuote("Watch Co", "WATCH", "stock", current_price=88.0)
    histories = {
        "STOP": pd.DataFrame({"Date": dates, "Price": [100.0, 95.0, 80.0]}),
        "WATCH": pd.DataFrame({"Date": dates, "Price": [100.0, 96.0, 88.0]}),
    }
    report = ReportBuilder(timezone_name="UTC").build(
        "Brief", [], portfolio_quotes=[stop, watch],
        stop_loss_history=histories,
    )
    assert "STOP-LOSS MONITOR" in report.html
    assert "-20.00%" in report.html
    assert "-12.00%" in report.html


def test_report_contains_momentum_top_25_table() -> None:
    momentum = [{
        "ticker": "NVDA", "rank": 1, "return_6m": 0.421,
        "return_12m": 0.753, "vol_6m": 0.30, "vol_12m": 0.35,
        "score_6m": 3.2, "score_12m": 3.8, "momentum_score": 3.5,
    }]
    report = ReportBuilder(timezone_name="UTC").build(
        "Brief", [], momentum_top25=momentum,
    )
    assert "MOMENTUM TOP 25" in report.html
    assert "NVDA" in report.html
    assert "+42.1%" in report.html
    assert "+75.3%" in report.html
    assert "+3.50" in report.html


def test_report_contains_saved_monthly_momentum_changes() -> None:
    changes = {
        "entered": ["NEW1", "NEW2"],
        "exited": ["OLD1", "OLD2"],
        "remained": ["KEEP"],
        "rank_changes": [{
            "ticker": "KEEP", "previous_rank": 3, "current_rank": 1, "change": 2,
        }],
    }
    report = ReportBuilder(timezone_name="UTC").build(
        "Brief", [], momentum_changes=changes,
        momentum_comparison_date="2026-07-26",
    )
    assert "MOMENTUM CHANGES VS 1 MONTH AGO" in report.html
    assert "2026-07-26" in report.html
    assert "DEMO COMPARISON" not in report.html
    assert "NEW1, NEW2" in report.html
    assert "OLD1, OLD2" in report.html
    assert "KEEP" in report.html


def test_report_contains_100_percent_sector_distribution_comparison() -> None:
    sectors = [{
        "sector": "Technology", "current_count": 10, "previous_count": 5,
        "three_month_count": 15, "current_pct": 40.0, "previous_pct": 20.0,
        "three_month_pct": 60.0, "change_pp": 20.0, "change_3m_pp": -20.0,
        "color": "#3a7059",
    }, {
        "sector": "Healthcare", "current_count": 15, "previous_count": 20,
        "three_month_count": 10, "current_pct": 60.0, "previous_pct": 80.0,
        "three_month_pct": 40.0, "change_pp": -20.0, "change_3m_pp": 20.0,
        "color": "#65a9a1",
    }]
    report = ReportBuilder(timezone_name="UTC").build(
        "Brief", [], momentum_sector_comparison=sectors,
        momentum_comparison_date="2026-07-26", momentum_sector_3m_date="2026-05-26",
    )
    assert "MOMENTUM TOP 25 SECTOR DISTRIBUTION" in report.html
    assert "Technology 40.0%" in report.html
    assert "Healthcare 80.0%" in report.html
    assert "+20.0 pp" in report.html
    assert "3 MONTHS AGO" in report.html
    assert "2026-05-26" in report.html
