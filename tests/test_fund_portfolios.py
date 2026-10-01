from datetime import date

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from config.settings import AppSettings, BASE_DIR
from data import internal_data_source as source
from workflows.daily_brief import load_brief_data, build_report, prepare_email_payload
from workflows import daily_brief as workflow


@pytest.fixture
def fund_feed(monkeypatch, tmp_path):
    holdings = pd.DataFrame([
        {"fund_id": "A", "fund_name": "Alpha <Fund>", "isin": "ISIN1"},
        {"fund_id": "B", "fund_name": "Beta Fund", "isin": "ISIN1"},
        {"fund_id": "B", "fund_name": "Beta Fund", "isin": "ISIN2"},
        {"fund_id": "B", "fund_name": "Beta Fund", "isin": "ISIN2"},
    ])
    monkeypatch.setattr(source, "load_portfolio_isins", lambda as_of: holdings.copy())
    monkeypatch.setenv("MOMENTUM_DB_PATH", str(tmp_path / "momentum.db"))
    monkeypatch.setattr(source, "load_sp500_universe", lambda as_of: pd.DataFrame([
        {"ticker": "ONE", "data_id": "ONE.RIC", "sector": "Technology"},
        {"ticker": "TWO", "data_id": "TWO.RIC", "sector": "Energy"},
    ]))
    dates = pd.bdate_range(end="2026-10-01", periods=300)
    steps = np.arange(300)
    prices = pd.concat([pd.DataFrame({
        "isin": isin, "ticker": ticker, "data_id": ticker + ".RIC",
        "date": dates, "close": 100 * np.exp(.001 * steps + .02 * np.sin(steps + i)),
    }) for i, (isin, ticker) in enumerate([("ISIN1", "ONE"), ("ISIN2", "TWO")])])
    calls = []

    def load_prices(isins, as_of):
        calls.append(list(isins))
        return prices.copy()

    monkeypatch.setattr(source, "load_portfolio_prices", load_prices)
    monkeypatch.setattr(source, "load_momentum_prices", lambda ids, as_of: prices[["data_id", "date", "close"]].rename(columns={"close": "value"}))
    monkeypatch.setattr("reports.report_builder.build_performer_history_image", lambda *a, **kw: (None, None))
    return holdings, calls


def test_shared_isin_fetched_once_and_included_in_each_fund(fund_feed):
    _, calls = fund_feed
    data = load_brief_data(date(2026, 10, 1), AppSettings())
    assert calls == [["ISIN1", "ISIN2"]]
    assert len(data.quotes) == 2
    assert len(data.portfolio_rows) == 3
    assert [(f["fund_id"], [q.ticker for q in f["quotes"]]) for f in data.fund_portfolios] == [
        ("A", ["ONE"]), ("B", ["ONE", "TWO"]),
    ]
    report = build_report(data, AppSettings())
    assert "Alpha &lt;Fund&gt;" in report.html
    alpha, beta = report.html.split("Alpha &lt;Fund&gt;", 1)[1].split("Beta Fund", 1)
    assert "ONE" in alpha and "TWO" not in alpha
    assert "ONE" in beta and "TWO" in beta
    assert report.html.count("PORTFOLIO STATUS") == 2
    assert report.html.count("MOMENTUM TOP 25") == 1
    assert report.html.count("MOMENTUM TOP 100 SECTOR DISTRIBUTION") == 1


@pytest.mark.parametrize("column", ["fund_id", "fund_name", "isin"])
def test_missing_membership_columns_are_rejected(fund_feed, monkeypatch, column):
    holdings, calls = fund_feed
    monkeypatch.setattr(source, "load_portfolio_isins", lambda as_of: holdings.drop(columns=column))
    with pytest.raises(ValueError, match=column):
        source.build_internal_data_provider(date(2026, 10, 1))
    assert not calls


def test_conflicting_fund_names_are_rejected(fund_feed, monkeypatch):
    holdings, _ = fund_feed
    holdings.loc[2, "fund_name"] = "Different Fund"
    with pytest.raises(ValueError, match="fund_id"):
        source.build_internal_data_provider(date(2026, 10, 1))


@pytest.mark.parametrize("column,value", [("fund_id", " "), ("fund_name", None), ("isin", "")])
def test_empty_membership_values_are_rejected(fund_feed, column, value):
    holdings, calls = fund_feed
    holdings.loc[0, column] = value
    with pytest.raises(ValueError, match=column):
        source.build_internal_data_provider(date(2026, 10, 1))
    assert not calls


def test_airflow_sends_one_report_with_all_funds(fund_feed, monkeypatch):
    deliveries = []
    def deliver(report, to, cc, bcc):
        deliveries.append((report, to))
        return "Email sent successfully!"
    monkeypatch.setattr(workflow, "deliver_report", deliver)
    result = workflow.run_daily_brief("2026-10-01", to_address=["test@example.com"])
    assert len(deliveries) == 1
    assert deliveries[0][1] == ["test@example.com"]
    assert "Alpha &lt;Fund&gt;" in deliveries[0][0].html and "Beta Fund" in deliveries[0][0].html
    assert result["portfolio_size"] == 2
    assert result["fund_count"] == 2
    assert result["fund_position_count"] == 3


def test_fund_chart_attachments_have_unique_paths_and_cids(fund_feed, monkeypatch, tmp_path):
    keys = []

    def chart(histories, quotes, output_dir, now, group_key, color):
        keys.append(group_key)
        return (str(tmp_path / (group_key + ".png")), group_key), None

    monkeypatch.setattr("reports.report_builder.build_performer_history_image", chart)
    data = load_brief_data(date(2026, 10, 1), AppSettings())
    report = build_report(data, AppSettings())
    assert len(keys) == len(set(keys)) == 4
    assert len(report.inline_images) == 4
    html, paths = prepare_email_payload(report)
    assert len(set(paths)) == 4
    for i in range(1, 5):
        assert f"cid:inline_image_{i}" in html


def test_streamlit_has_fund_tabs_and_one_common_momentum_section(fund_feed):
    _, calls = fund_feed
    app = AppTest.from_file(str(BASE_DIR / "app.py"), default_timeout=20).run()
    next(b for b in app.button if b.label == "Refresh Data").click().run()
    assert not app.exception
    labels = [t.label for t in app.tabs]
    assert "Alpha <Fund> (A)" in labels and "Beta Fund (B)" in labels
    assert [s.value for s in app.subheader].count("Momentum Top 25") == 1
    next(b for b in app.button if b.label == "Generate Brief").click().run()
    assert not app.exception
    assert "Beta Fund" in app.session_state.report.html
    assert calls == [["ISIN1", "ISIN2"]]
