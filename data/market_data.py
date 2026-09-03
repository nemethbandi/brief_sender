from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
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


class YahooFinanceProvider(MarketDataProvider):
    provider_name = "yahoo"
    # Yahoo occasionally exposes the same index through only one exchange code.
    # Candidates are tried in order while the configured ticker remains the
    # canonical identifier shown in the UI and report.
    TICKER_FALLBACKS: dict[str, tuple[str, ...]] = {
        "000300.SS": ("399300.SZ",),
    }

    @staticmethod
    def _history(yf: Any, ticker_symbol: str) -> Any:
        """Return at least two closes, widening the window when necessary."""
        last_history = None
        for period in ("5d", "1mo"):
            history = yf.Ticker(ticker_symbol).history(
                period=period,
                interval="1d",
                timeout=12,
                auto_adjust=False,
            )
            last_history = history
            if not history.empty and "Close" in history and len(history["Close"].dropna()) >= 2:
                return history
        return last_history

    def get_quote(self, asset: dict[str, Any]) -> MarketQuote:
        quote = MarketQuote(
            asset["name"],
            asset["ticker"],
            asset["asset_class"],
            display_format=asset.get("format", "number"),
            region=asset.get("region", "Other"),
            threshold_pct=float(asset["threshold_pct"]) if asset.get("threshold_pct") is not None else None,
        )
        try:
            import yfinance as yf

            candidates = (asset["ticker"], *self.TICKER_FALLBACKS.get(asset["ticker"], ()))
            history = None
            closes = []
            errors: list[str] = []
            for candidate in candidates:
                try:
                    candidate_history = self._history(yf, candidate)
                    candidate_closes = (
                        candidate_history["Close"].dropna()
                        if candidate_history is not None
                        and not candidate_history.empty
                        and "Close" in candidate_history
                        else []
                    )
                    if len(candidate_closes) >= 2:
                        history, closes = candidate_history, candidate_closes
                        if candidate != asset["ticker"]:
                            logger.info("Using Yahoo fallback %s for %s", candidate, asset["ticker"])
                        break
                    errors.append(f"{candidate}: fewer than two closes")
                except Exception as exc:
                    errors.append(f"{candidate}: {exc}")
            if history is None or len(closes) < 2:
                raise ValueError("No usable Yahoo history (" + "; ".join(errors) + ")")
            current, previous = float(closes.iloc[-1]), float(closes.iloc[-2])
            quote.current_price = current
            quote.previous_close = previous
            quote.absolute_change = current - previous
            quote.percentage_change = percentage_change(current, previous)
            last_index = closes.index[-1]
            quote.timestamp = last_index.to_pydatetime() if hasattr(last_index, "to_pydatetime") else datetime.now(timezone.utc)
        except Exception as exc:
            quote.error = str(exc)
            logger.warning("Market data failed for %s: %s", asset["ticker"], exc)
        return quote

    def get_history(self, ticker: str, period: str = "3mo") -> Any:
        import pandas as pd
        try:
            import yfinance as yf
            period = period if period in {"1mo", "3mo", "6mo", "1y", "2y"} else "3mo"
            history = yf.Ticker(ticker).history(
                period=period, interval="1d", timeout=15, auto_adjust=True
            )
            if history.empty or "Close" not in history:
                raise ValueError("No historical closing prices returned")
            dates = pd.to_datetime(history.index)
            if getattr(dates, "tz", None) is not None:
                dates = dates.tz_localize(None)
            return pd.DataFrame({"Date": dates, "Price": history["Close"].astype(float).to_numpy()}).dropna()
        except Exception as exc:
            logger.warning("Historical prices failed for %s: %s", ticker, exc)
            return pd.DataFrame(columns=["Date", "Price"])

    def get_momentum_universe(self) -> list[str]:
        from processing import momentum
        return momentum.get_universe()

    def get_momentum_prices(
        self, tickers: Iterable[str], period: str = "15mo",
    ) -> Any:
        from processing import momentum
        return momentum.download_prices(tickers, period=period)

    def get_security_metadata(
        self, tickers: Iterable[str],
    ) -> list[dict[str, str]]:
        from processing import momentum
        return momentum.download_security_metadata(tickers)


def create_market_data_provider(name: str | None = None) -> MarketDataProvider:
    """Build the configured provider without importing optional SDKs eagerly."""
    import os

    selected = (name or os.getenv("MARKET_DATA_PROVIDER", "yahoo")).strip().lower()
    if selected in {"yahoo", "yfinance"}:
        return YahooFinanceProvider()
    if selected in {"lseg", "datastream"}:
        from data.lseg_provider import LSEGDataProvider
        return LSEGDataProvider()
    raise ValueError(
        f"Unsupported MARKET_DATA_PROVIDER={selected!r}; expected 'yahoo' or 'lseg'"
    )


class MarketDataService:
    def __init__(self, provider: MarketDataProvider | None = None) -> None:
        self.provider = provider or create_market_data_provider()

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
