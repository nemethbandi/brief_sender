from datetime import date

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from config.settings import AppSettings, BASE_DIR
from data import internal_data_source as source
from workflows import daily_brief as workflow


@pytest.fixture
def internal_feed(monkeypatch, tmp_path):
    calls = []
    dates = pd.bdate_range(end="2026-09-18", periods=300)
    values = 100 * np.exp(np.arange(300) * .001 + .02 * np.sin(np.arange(300)))
    monkeypatch.setenv("MOMENTUM_DB_PATH", str(tmp_path / "momentum.db"))
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "yahoo")  # Obsolete settings must not select another source.

    def isins(as_of):
        calls.append("isins")
        return ["US5949181045"]

    def universe(as_of):
        calls.append("universe")
        return pd.DataFrame([dict(ticker="MSFT", data_id="MSFT.O", sector="Technology", industry="Software")])

    def portfolio(isins, as_of):
        calls.append("portfolio")
        assert isins == ["US5949181045"]
        return pd.DataFrame(dict(isin="US5949181045", ticker="MSFT", data_id="MSFT.O", date=dates, close=values))

    def momentum(data_ids, as_of):
        calls.append("momentum")
        assert data_ids == ["MSFT.O"]
        return pd.DataFrame(dict(data_id="MSFT.O", date=dates, value=values))

    monkeypatch.setattr(source, "load_portfolio_isins", isins)
    monkeypatch.setattr(source, "load_sp500_universe", universe)
    monkeypatch.setattr(source, "load_portfolio_prices", portfolio)
    monkeypatch.setattr(source, "load_momentum_prices", momentum)
    return calls


def test_shared_load_uses_only_internal_feed(internal_feed, tmp_path):
    data = workflow.load_brief_data(date(2026, 9, 18), AppSettings())
    assert internal_feed == ["isins", "universe", "portfolio", "momentum"]
    assert data.portfolio_rows[0]["data_id"] == "MSFT.O"
    assert data.quotes[0].ticker == "MSFT"
    assert not data.histories["MSFT"].empty
    assert data.momentum["ranking"]["ticker"].tolist() == ["MSFT"]
    assert data.momentum["changes"] is None  # No generated historical market data.
    assert data.momentum["one_month_date"] is None
    assert data.momentum["three_month_date"] is None
    assert (tmp_path / "momentum.db").exists()


def test_airflow_workflow_uses_internal_feed_and_explicit_recipient(internal_feed, monkeypatch):
    deliveries = []
    monkeypatch.setattr(workflow, "deliver_report", lambda report, to, cc, bcc: deliveries.append(to) or "Email sent successfully!")
    monkeypatch.setattr("reports.report_builder.build_performer_history_image", lambda *a, **kw: (None, None))
    result = workflow.run_daily_brief("2026-09-18", to_address=["test@example.com"])
    assert internal_feed == ["isins", "universe", "portfolio", "momentum"]
    assert result["portfolio_size"] == result["momentum_rows"] == 1
    assert deliveries == [["test@example.com"]]


def test_streamlit_refresh_and_preview_use_internal_feed(internal_feed, monkeypatch):
    monkeypatch.setattr("reports.report_builder.build_performer_history_image", lambda *a, **kw: (None, None))
    app = AppTest.from_file(str(BASE_DIR / "app.py"), default_timeout=20).run()
    assert not app.exception
    next(b for b in app.button if b.label == "Refresh Data").click().run()
    assert not app.exception
    assert internal_feed == ["isins", "universe", "portfolio", "momentum"]
    assert app.session_state.portfolio_quotes[0].ticker == "MSFT"
    next(b for b in app.button if b.label == "Generate Brief").click().run()
    assert not app.exception
    assert "MSFT" in app.session_state.report.html
    assert "DEMO" not in app.session_state.report.html
    assert "N/A" in app.session_state.report.html
    next(b for b in app.button if b.label == "Preview Email").click().run()
    assert not app.exception
    assert app.session_state.show_preview
    assert internal_feed == ["isins", "universe", "portfolio", "momentum"]
    # A failed refresh must invalidate an older report and quotes.
    monkeypatch.setattr(source, "load_portfolio_isins", lambda as_of: [])
    next(b for b in app.button if b.label == "Refresh Data").click().run()
    assert not app.exception
    assert app.error
    assert app.session_state.report is None
    assert not app.session_state.portfolio_quotes


def test_empty_loaders_fail_before_email(monkeypatch):
    monkeypatch.setattr(source, "load_portfolio_isins", lambda as_of: [])
    monkeypatch.setattr(workflow, "deliver_report", lambda *a: pytest.fail("Must not send"))
    with pytest.raises(RuntimeError, match="load_portfolio_isins"):
        workflow.run_daily_brief("2026-09-18", to_address=["test@example.com"])
