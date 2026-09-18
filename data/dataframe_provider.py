from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from data.market_data import MarketDataProvider, percentage_change
from data.models import MarketQuote


def _require_columns(frame: pd.DataFrame, required: set[str], name: str) -> None:
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{name} is missing required columns: {sorted(missing)}")


def _normalize_identifier(value: Any) -> str:
    return str(value).strip()


def _normalize_ticker(value: Any) -> str:
    return str(value).strip().upper().replace(".", "-")


def _normalize_price_frame(
    frame: pd.DataFrame, value_column: str, name: str,
) -> pd.DataFrame:
    _require_columns(frame, {"data_id", "date", value_column}, name)
    normalized = frame[["data_id", "date", value_column]].copy()
    normalized["data_id"] = normalized["data_id"].map(_normalize_identifier)
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce", utc=True)
    normalized["date"] = normalized["date"].dt.tz_localize(None)
    normalized[value_column] = pd.to_numeric(normalized[value_column], errors="coerce")
    normalized = normalized.dropna(subset=["date", value_column])
    normalized = normalized[normalized["data_id"] != ""]
    if normalized.duplicated(["data_id", "date"]).any():
        duplicates = normalized.loc[
            normalized.duplicated(["data_id", "date"], keep=False), "data_id",
        ].unique()
        raise ValueError(
            f"{name} contains duplicate data_id/date rows: {list(duplicates[:10])}"
        )
    return normalized.sort_values(["data_id", "date"]).reset_index(drop=True)


class DataFrameMarketDataProvider(MarketDataProvider):
    """Serve already-extracted internal/LSEG data to the existing business logic.

    Extraction, authentication and SQL/API details deliberately live outside
    this class. The provider only validates and normalizes three DataFrames.
    """

    provider_name = "dataframe"

    def __init__(
        self,
        universe: pd.DataFrame,
        portfolio_prices: pd.DataFrame,
        momentum_prices: pd.DataFrame,
    ) -> None:
        _require_columns(universe, {"ticker", "data_id"}, "universe")
        normalized_universe = universe.copy()
        normalized_universe["ticker"] = normalized_universe["ticker"].map(
            _normalize_ticker,
        )
        normalized_universe["data_id"] = normalized_universe["data_id"].map(
            _normalize_identifier,
        )
        for column in ("sector", "industry"):
            if column not in normalized_universe:
                normalized_universe[column] = "Unknown"
            normalized_universe[column] = (
                normalized_universe[column].fillna("Unknown").replace("", "Unknown")
            )
        normalized_universe = normalized_universe[
            (normalized_universe["ticker"] != "")
            & (normalized_universe["data_id"] != "")
        ]
        if normalized_universe["ticker"].duplicated().any():
            raise ValueError("universe contains duplicate canonical tickers")
        if normalized_universe["data_id"].duplicated().any():
            raise ValueError("universe contains duplicate data_id values")

        self.universe = normalized_universe.reset_index(drop=True)
        self.portfolio_prices = _normalize_price_frame(
            portfolio_prices, "close", "portfolio_prices",
        )
        self.momentum_prices = _normalize_price_frame(
            momentum_prices, "value", "momentum_prices",
        )
        self._data_id_by_ticker = dict(zip(
            self.universe["ticker"], self.universe["data_id"],
        ))

    @staticmethod
    def _period_days(period: str) -> int:
        return {
            "5d": 10,
            "1mo": 40,
            "3mo": 110,
            "6mo": 220,
            "1y": 400,
            "2y": 800,
            "15mo": 500,
        }.get(period, 110)

    def _portfolio_series(self, data_id: str, period: str) -> pd.Series:
        identifier = _normalize_identifier(data_id)
        selected = self.portfolio_prices[
            self.portfolio_prices["data_id"] == identifier
        ]
        if selected.empty:
            return pd.Series(dtype=float)
        end = selected["date"].max()
        start = end - pd.Timedelta(days=self._period_days(period))
        selected = selected[selected["date"] >= start]
        return selected.set_index("date")["close"].sort_index()

    def get_quote(self, asset: dict[str, Any]) -> MarketQuote:
        quote = MarketQuote(
            asset["name"], asset["ticker"], asset["asset_class"],
            display_format=asset.get("format", "number"),
            region=asset.get("region", "Other"),
            threshold_pct=(
                float(asset["threshold_pct"])
                if asset.get("threshold_pct") is not None else None
            ),
        )
        data_id = str(asset.get("data_id") or asset.get("ric") or asset["ticker"])
        closes = self._portfolio_series(data_id, "1mo").dropna()
        if len(closes) < 2:
            quote.error = f"Fewer than two portfolio closes for data_id={data_id}"
            return quote
        current, previous = float(closes.iloc[-1]), float(closes.iloc[-2])
        quote.current_price = current
        quote.previous_close = previous
        quote.absolute_change = current - previous
        quote.percentage_change = percentage_change(current, previous)
        quote.timestamp = closes.index[-1].to_pydatetime()
        return quote

    def get_history(self, ticker: str, period: str = "3mo") -> pd.DataFrame:
        series = self._portfolio_series(ticker, period).dropna()
        return pd.DataFrame({"Date": series.index, "Price": series.to_numpy()})

    def get_momentum_universe(self) -> list[str]:
        return sorted(self.universe["ticker"].tolist())

    def get_momentum_prices(
        self, tickers: Iterable[str], period: str = "15mo",
    ) -> pd.DataFrame:
        requested = [_normalize_ticker(ticker) for ticker in tickers]
        missing = [ticker for ticker in requested if ticker not in self._data_id_by_ticker]
        if missing:
            raise ValueError(f"Momentum tickers missing from universe: {missing[:10]}")
        data_ids = [self._data_id_by_ticker[ticker] for ticker in requested]
        selected = self.momentum_prices[
            self.momentum_prices["data_id"].isin(data_ids)
        ].copy()
        if selected.empty:
            return pd.DataFrame(columns=requested)
        end = selected["date"].max()
        start = end - pd.Timedelta(days=self._period_days(period))
        selected = selected[selected["date"] >= start]
        prices = selected.pivot(index="date", columns="data_id", values="value")
        ticker_by_data_id = {
            data_id: ticker for ticker, data_id in self._data_id_by_ticker.items()
        }
        prices = prices.rename(columns=ticker_by_data_id)
        return prices.reindex(columns=requested).sort_index()

    def get_security_metadata(
        self, tickers: Iterable[str],
    ) -> list[dict[str, str]]:
        metadata = self.universe.set_index("ticker")
        rows: list[dict[str, str]] = []
        for raw_ticker in tickers:
            ticker = _normalize_ticker(raw_ticker)
            if ticker not in metadata.index:
                rows.append({
                    "ticker": ticker, "sector": "Unknown", "industry": "Unknown",
                })
                continue
            row = metadata.loc[ticker]
            rows.append({
                "ticker": ticker,
                "sector": str(row["sector"]),
                "industry": str(row["industry"]),
            })
        return rows
