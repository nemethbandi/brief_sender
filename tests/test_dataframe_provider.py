from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.dataframe_provider import DataFrameMarketDataProvider
from data.market_data import MarketDataService


def frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    universe = pd.DataFrame({
        "ticker": ["MSFT", "BRK.A"],
        "data_id": ["MSFT.O", "BRKa.N"],
        "sector": ["Technology", "Financial Services"],
        "industry": ["Software", "Investment Banking"],
    })
    portfolio_dates = pd.bdate_range("2026-01-02", periods=160)
    portfolio_prices = pd.DataFrame({
        "data_id": ["MSFT.O"] * len(portfolio_dates),
        "date": portfolio_dates,
        "close": np.arange(len(portfolio_dates), dtype=float) + 100.0,
    })
    momentum_dates = pd.bdate_range("2025-06-02", periods=300)
    momentum_prices = pd.concat([
        pd.DataFrame({
            "data_id": data_id,
            "date": momentum_dates,
            "value": np.arange(len(momentum_dates), dtype=float) + start,
        })
        for data_id, start in (("MSFT.O", 100.0), ("BRKa.N", 200.0))
    ], ignore_index=True)
    return universe, portfolio_prices, momentum_prices


def test_dataframe_provider_serves_all_business_inputs() -> None:
    provider = DataFrameMarketDataProvider(*frames())
    assert provider.get_momentum_universe() == ["BRK-A", "MSFT"]

    prices = provider.get_momentum_prices(["MSFT", "BRK-A"])
    assert prices.columns.tolist() == ["MSFT", "BRK-A"]
    assert len(prices) == 300

    metadata = provider.get_security_metadata(["BRK-A", "MSFT"])
    assert metadata[0] == {
        "ticker": "BRK-A",
        "sector": "Financial Services",
        "industry": "Investment Banking",
    }

    service = MarketDataService(provider)
    asset = {
        "ticker": "MSFT", "data_id": "MSFT.O", "name": "Microsoft",
        "asset_class": "stock", "threshold_pct": 2.0,
    }
    quote = service.get_quotes([asset])[0]
    assert quote.ticker == "MSFT"
    assert quote.current_price == 259.0
    assert quote.previous_close == 258.0
    history = service.get_histories([asset], "3mo")["MSFT"]
    assert not history.empty
    assert history.columns.tolist() == ["Date", "Price"]


def test_dataframe_provider_rejects_ambiguous_duplicate_prices() -> None:
    universe, portfolio_prices, momentum_prices = frames()
    duplicated = pd.concat([portfolio_prices, portfolio_prices.iloc[[0]]])
    with pytest.raises(ValueError, match="duplicate data_id/date"):
        DataFrameMarketDataProvider(universe, duplicated, momentum_prices)


def test_dataframe_provider_rejects_missing_contract_columns() -> None:
    universe, portfolio_prices, momentum_prices = frames()
    with pytest.raises(ValueError, match="data_id"):
        DataFrameMarketDataProvider(
            universe.drop(columns=["data_id"]), portfolio_prices, momentum_prices,
        )
