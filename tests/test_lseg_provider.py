from __future__ import annotations

import pandas as pd

from data.lseg_provider import LSEGDataProvider


class FakeLSEGClient:
    def __init__(self) -> None:
        self.history_calls: list[list[str]] = []

    def get_data(self, universe, fields):
        if fields == ["TR.RIC", "TR.ExchangeTicker"]:
            return pd.DataFrame({
                "Instrument": ["MSFT.O", "BRKa.N"],
                "RIC": ["MSFT.O", "BRKa.N"],
                "Exchange Ticker": ["MSFT", "BRK.A"],
            })
        return pd.DataFrame({
            "Instrument": list(universe),
            "TRBC Economic Sector Name": ["Technology", "Financials"],
            "TRBC Industry Name": ["Software", "Investment Banking"],
        })

    def get_history(self, universe, fields, start, end, interval):
        self.history_calls.append(list(universe))
        dates = pd.bdate_range("2026-08-27", periods=3)
        columns = pd.MultiIndex.from_tuples([
            (instrument, "TRDPRC_1") for instrument in universe
        ])
        values = [
            [100.0 + column_index + day_index for column_index in range(len(universe))]
            for day_index in range(3)
        ]
        return pd.DataFrame(values, index=dates, columns=columns)


def provider() -> tuple[LSEGDataProvider, FakeLSEGClient]:
    client = FakeLSEGClient()
    return LSEGDataProvider(client=client, min_universe_size=1), client


def test_lseg_universe_maps_exchange_tickers_to_rics() -> None:
    adapter, _ = provider()
    assert adapter.get_momentum_universe() == ["BRK-A", "MSFT"]
    prices = adapter.get_momentum_prices(["MSFT", "BRK-A"])
    assert prices.columns.tolist() == ["MSFT", "BRK-A"]
    assert prices["MSFT"].tolist() == [100.0, 101.0, 102.0]


def test_lseg_portfolio_quote_uses_data_id_but_keeps_display_ticker() -> None:
    adapter, client = provider()
    quote = adapter.get_quote({
        "ticker": "MSFT",
        "data_id": "MSFT.O",
        "name": "Microsoft",
        "asset_class": "stock",
        "threshold_pct": 2.5,
    })
    assert client.history_calls[-1] == ["MSFT.O"]
    assert quote.ticker == "MSFT"
    assert quote.current_price == 102.0
    assert quote.previous_close == 101.0


def test_lseg_metadata_is_normalized_to_report_contract() -> None:
    adapter, _ = provider()
    adapter.get_momentum_universe()
    assert adapter.get_security_metadata(["MSFT", "BRK-A"]) == [
        {"ticker": "MSFT", "sector": "Technology", "industry": "Software"},
        {
            "ticker": "BRK-A",
            "sector": "Financial Services",
            "industry": "Investment Banking",
        },
    ]
