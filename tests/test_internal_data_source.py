from datetime import date

import pandas as pd

import data.internal_data_source as source
from data.internal_data_source import (
    MOMENTUM_PRICE_COLUMNS, PORTFOLIO_PRICE_COLUMNS, UNIVERSE_COLUMNS,
    load_momentum_prices, load_portfolio_isins,
    load_portfolio_prices, load_sp500_universe,
)


def test_empty_internal_loader_templates_expose_the_required_contract() -> None:
    as_of = date(2026, 9, 3)
    assert load_portfolio_isins(as_of) == []
    assert load_sp500_universe(as_of).columns.tolist() == UNIVERSE_COLUMNS
    assert load_portfolio_prices([], as_of).columns.tolist() == PORTFOLIO_PRICE_COLUMNS
    assert load_momentum_prices([], as_of).columns.tolist() == MOMENTUM_PRICE_COLUMNS


def test_unique_sql_isins_are_passed_to_portfolio_price_loader(monkeypatch) -> None:
    as_of = date(2026, 9, 3)
    received_isins: list[str] = []
    monkeypatch.setattr(
        source, "load_portfolio_isins",
        lambda value: ["us5949181045", "US5949181045", ""],
    )
    monkeypatch.setattr(
        source, "load_sp500_universe",
        lambda value: pd.DataFrame([{
            "ticker": "MSFT", "data_id": "MSFT.O",
            "sector": "Technology", "industry": "Software",
        }]),
    )

    def portfolio_prices(isins, value):
        received_isins.extend(isins)
        return pd.DataFrame([
            {
                "isin": "US5949181045", "ticker": "MSFT", "data_id": "MSFT.O",
                "name": "Microsoft", "threshold_pct": 2.0,
                "date": "2026-09-01", "close": 100.0,
            },
            {
                "isin": "US5949181045", "ticker": "MSFT", "data_id": "MSFT.O",
                "name": "Microsoft", "threshold_pct": 2.0,
                "date": "2026-09-02", "close": 101.0,
            },
        ])

    monkeypatch.setattr(source, "load_portfolio_prices", portfolio_prices)
    monkeypatch.setattr(
        source, "load_momentum_prices",
        lambda data_ids, value: pd.DataFrame([
            {"data_id": "MSFT.O", "date": "2026-09-01", "value": 100.0},
            {"data_id": "MSFT.O", "date": "2026-09-02", "value": 101.0},
        ]),
    )

    rows, provider = source.build_internal_data_provider(as_of)
    assert received_isins == ["US5949181045"]
    assert rows == [{
        "isin": "US5949181045", "ticker": "MSFT", "data_id": "MSFT.O",
        "name": "Microsoft", "threshold_pct": 2.0,
    }]
    assert provider.get_momentum_universe() == ["MSFT"]
