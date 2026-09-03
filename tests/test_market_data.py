from data.market_data import MarketDataProvider, MarketDataService, percentage_change
from data.models import MarketQuote
from processing.market_analyzer import select_portfolio_performers, select_stop_loss_signals
import pytest
import pandas as pd


class FakeProvider(MarketDataProvider):
    def get_quote(self, asset):
        return MarketQuote(asset["name"], asset["ticker"], asset["asset_class"], 102, 100, 2, 2)

    def get_history(self, ticker, period="3mo"):
        return pd.DataFrame({"Date": pd.to_datetime(["2026-01-01", "2026-01-02"]), "Price": [100.0, 102.0]})


def test_percentage_change() -> None:
    assert percentage_change(105, 100) == pytest.approx(5)
    assert percentage_change(95, 100) == pytest.approx(-5)
    assert percentage_change(None, 100) is None
    assert percentage_change(100, 0) is None


def test_provider_is_replaceable() -> None:
    quote = MarketDataService(FakeProvider()).get_quotes([{"name":"Test","ticker":"T","asset_class":"stock"}])[0]
    assert quote.percentage_change == 2
    history = MarketDataService(FakeProvider()).get_histories([{"ticker": "T"}], "3mo")["T"]
    assert history["Price"].tolist() == [100.0, 102.0]


def test_portfolio_notable_moves_use_each_tickers_threshold() -> None:
    low_threshold = MarketQuote("Alpha", "AAA", "stock", percentage_change=1.2, threshold_pct=1.0)
    high_threshold = MarketQuote("Beta", "BBB", "stock", percentage_change=1.9, threshold_pct=2.0)
    negative = MarketQuote("Gamma", "CCC", "stock", percentage_change=-2.6, threshold_pct=2.5)
    assert [quote.ticker for quote in MarketDataService.notable_portfolio_moves(
        [low_threshold, high_threshold, negative]
    )] == ["CCC", "AAA"]


def test_top_and_worst_performers_are_ranked_by_daily_change() -> None:
    quotes = [
        MarketQuote("Alpha", "AAA", "stock", percentage_change=1.0),
        MarketQuote("Beta", "BBB", "stock", percentage_change=-3.0),
        MarketQuote("Gamma", "CCC", "stock", percentage_change=4.0),
        MarketQuote("Missing", "N/A", "stock"),
    ]
    best, worst = select_portfolio_performers(quotes, limit=2)
    assert [quote.ticker for quote in best] == ["CCC", "AAA"]
    assert [quote.ticker for quote in worst] == ["BBB", "AAA"]


def test_stop_loss_signals_use_latest_six_month_peak() -> None:
    dates = pd.to_datetime(["2026-03-01", "2026-04-01", "2026-08-20"])
    histories = {
        "STOP": pd.DataFrame({"Date": dates, "Price": [100.0, 100.0, 84.0]}),
        "WATCH": pd.DataFrame({"Date": dates, "Price": [100.0, 98.0, 88.0]}),
        "OK": pd.DataFrame({"Date": dates, "Price": [100.0, 99.0, 96.0]}),
    }
    quotes = [
        MarketQuote("Stop", "STOP", "stock", current_price=84.0),
        MarketQuote("Watch", "WATCH", "stock", current_price=88.0),
        MarketQuote("Okay", "OK", "stock", current_price=96.0),
    ]
    signals = select_stop_loss_signals(quotes, histories)
    assert [(signal.quote.ticker, signal.status) for signal in signals] == [
        ("STOP", "STOP"), ("WATCH", "WATCH"),
    ]
    assert signals[0].drawdown_pct == pytest.approx(16.0)
    assert signals[0].peak_date.strftime("%Y-%m-%d") == "2026-04-01"
