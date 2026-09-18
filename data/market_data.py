from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

from data.models import MarketQuote
from utils.logger import get_logger

logger = get_logger(__name__)


def percentage_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


class MarketDataProvider(ABC):
    """Vendor-neutral contract consumed by the report and momentum workflows."""

    provider_name = "unknown"

    @abstractmethod
    def get_quote(self, asset: dict[str, Any]) -> MarketQuote: ...

    def get_history(self, ticker: str, period: str = "3mo") -> Any:
        raise NotImplementedError

    def get_momentum_universe(self) -> list[str]:
        raise NotImplementedError

    def get_momentum_prices(
        self, tickers: Iterable[str], period: str = "15mo",
    ) -> Any:
        raise NotImplementedError

    def get_security_metadata(
        self, tickers: Iterable[str],
    ) -> list[dict[str, str]]:
        raise NotImplementedError


class MarketDataService:
    def __init__(self, provider: MarketDataProvider) -> None:
        self.provider = provider

    def get_quotes(self, assets: list[dict[str, Any]]) -> list[MarketQuote]:
        return [self.provider.get_quote(asset) for asset in assets]

    def get_histories(self, assets: list[dict[str, Any]], period: str = "3mo") -> dict[str, Any]:
        import pandas as pd
        histories: dict[str, Any] = {}
        for asset in assets:
            try:
                provider_ticker = str(
                    asset.get("data_id") or asset.get("ric") or asset["ticker"]
                )
                histories[asset["ticker"]] = self.provider.get_history(provider_ticker, period)
            except Exception as exc:
                logger.warning("Historical provider failed for %s: %s", asset["ticker"], exc)
                histories[asset["ticker"]] = pd.DataFrame(columns=["Date", "Price"])
        return histories

    @staticmethod
    def notable_moves(quotes: list[MarketQuote], thresholds: dict[str, float]) -> list[MarketQuote]:
        result = [q for q in quotes if q.percentage_change is not None and abs(q.percentage_change) >= thresholds.get(q.asset_class, 1.0)]
        return sorted(result, key=lambda q: abs(q.percentage_change or 0), reverse=True)

    @staticmethod
    def notable_portfolio_moves(quotes: list[MarketQuote]) -> list[MarketQuote]:
        result = [
            quote for quote in quotes
            if quote.percentage_change is not None
            and abs(quote.percentage_change) >= (
                getattr(quote, "threshold_pct", None)
                if getattr(quote, "threshold_pct", None) is not None else 2.0
            )
        ]
        return sorted(result, key=lambda quote: abs(quote.percentage_change or 0), reverse=True)
