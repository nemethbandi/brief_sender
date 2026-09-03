from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from data.market_data import MarketDataProvider, percentage_change
from data.models import MarketQuote
from utils.logger import get_logger

logger = get_logger(__name__)

LSEG_SECTOR_NAMES = {
    "Consumer Cyclicals": "Consumer Cyclical",
    "Consumer Non-Cyclicals": "Consumer Defensive",
    "Financials": "Financial Services",
    "Telecommunications Services": "Communication Services",
}


def _period_start(period: str, end: datetime | None = None) -> tuple[str, str]:
    end_value = end or datetime.now(timezone.utc)
    days = {
        "5d": 10,
        "1mo": 40,
        "3mo": 110,
        "6mo": 220,
        "1y": 400,
        "2y": 800,
        "15mo": 500,
    }.get(period, 110)
    return (end_value - timedelta(days=days)).date().isoformat(), end_value.date().isoformat()


def _find_column(frame: pd.DataFrame, fragments: tuple[str, ...], fallback: int) -> Any:
    normalized = {
        str(column).strip().lower().replace(" ", "").replace("_", ""): column
        for column in frame.columns
    }
    for key, original in normalized.items():
        if any(fragment in key for fragment in fragments):
            return original
    if len(frame.columns) <= fallback:
        raise ValueError("LSEG response does not contain the expected columns")
    return frame.columns[fallback]


class LSEGDataProvider(MarketDataProvider):
    """LSEG Data Library adapter with a stable, vendor-neutral output contract.

    `client` and `universe_loader` are injectable so an internal Datastream/LSEG
    wrapper can be connected without changing report or momentum business logic.
    """

    provider_name = "lseg"

    def __init__(
        self,
        client: Any | None = None,
        universe_loader: Callable[[], pd.DataFrame] | None = None,
        min_universe_size: int | None = None,
    ) -> None:
        self._client = client
        self._session_opened = client is not None
        self._universe_loader = universe_loader
        self.sp500_chain = os.getenv("LSEG_SP500_UNIVERSE", "0#.SPX")
        self.close_field = os.getenv("LSEG_CLOSE_FIELD", "TRDPRC_1")
        self.momentum_field = os.getenv("LSEG_MOMENTUM_FIELD", self.close_field)
        self.ric_field = os.getenv("LSEG_RIC_FIELD", "TR.RIC")
        self.symbol_field = os.getenv("LSEG_SYMBOL_FIELD", "TR.ExchangeTicker")
        self.sector_field = os.getenv(
            "LSEG_SECTOR_FIELD", "TR.TRBCEconomicSector",
        )
        self.industry_field = os.getenv(
            "LSEG_INDUSTRY_FIELD", "TR.TRBCIndustry",
        )
        self.batch_size = max(1, int(os.getenv("LSEG_HISTORY_BATCH_SIZE", "50")))
        self.min_universe_size = (
            int(os.getenv("LSEG_MIN_UNIVERSE_SIZE", "400"))
            if min_universe_size is None else int(min_universe_size)
        )
        self._instrument_by_ticker: dict[str, str] = {}

    @staticmethod
    def _canonical_ticker(value: Any) -> str:
        return str(value).strip().upper().replace(".", "-")

    def _api(self) -> Any:
        if self._client is None:
            try:
                import lseg.data as ld
            except ImportError as exc:
                raise RuntimeError(
                    "LSEG provider selected but lseg-data is not installed"
                ) from exc
            self._client = ld
        if not self._session_opened:
            self._client.open_session()
            self._session_opened = True
        return self._client

    def _load_universe_frame(self) -> pd.DataFrame:
        if self._universe_loader is not None:
            frame = self._universe_loader()
        else:
            frame = self._api().get_data(
                universe=self.sp500_chain,
                fields=[self.ric_field, self.symbol_field],
            )
        if frame is None or frame.empty:
            raise RuntimeError("LSEG returned an empty S&P 500 universe")
        return frame

    def get_momentum_universe(self) -> list[str]:
        frame = self._load_universe_frame()
        ric_column = _find_column(frame, ("ric",), 1)
        symbol_column = _find_column(frame, ("exchangeticker", "ticker", "symbol"), 1)
        mapping: dict[str, str] = {}
        for _, row in frame.iterrows():
            ric = row.get(ric_column)
            symbol = row.get(symbol_column)
            if pd.isna(ric) or pd.isna(symbol):
                continue
            ticker = self._canonical_ticker(symbol)
            if ticker:
                mapping[ticker] = str(ric).strip()
        if not mapping:
            raise RuntimeError("LSEG S&P 500 universe contained no usable ticker/RIC pairs")
        if len(mapping) < self.min_universe_size:
            raise RuntimeError(
                "LSEG S&P 500 universe is unexpectedly small: "
                f"{len(mapping)} < {self.min_universe_size}"
            )
        self._instrument_by_ticker.update(mapping)
        logger.info("LSEG S&P 500 universe loaded: %d instruments", len(mapping))
        return sorted(mapping)

    def _history_matrix(
        self, instruments: list[str], period: str, field: str | None = None,
    ) -> pd.DataFrame:
        if not instruments:
            return pd.DataFrame()
        start, end = _period_start(period)
        result = self._api().get_history(
            universe=instruments,
            fields=[field or self.close_field],
            start=start,
            end=end,
            interval="1D",
        )
        if result is None or result.empty:
            return pd.DataFrame()
        frame = result.copy()
        frame.index = pd.to_datetime(frame.index).tz_localize(None)

        if isinstance(frame.columns, pd.MultiIndex):
            output: dict[str, pd.Series] = {}
            for instrument in instruments:
                matching = [
                    column for column in frame.columns
                    if instrument in {str(part) for part in column}
                ]
                if matching:
                    output[instrument] = pd.to_numeric(
                        frame[matching[0]], errors="coerce",
                    )
            return pd.DataFrame(output, index=frame.index)

        if len(instruments) == 1 and len(frame.columns) >= 1:
            return pd.DataFrame({
                instruments[0]: pd.to_numeric(frame.iloc[:, 0], errors="coerce"),
            }, index=frame.index)

        output = {}
        for instrument in instruments:
            matching = next(
                (column for column in frame.columns if str(column) == instrument), None,
            )
            if matching is not None:
                output[instrument] = pd.to_numeric(frame[matching], errors="coerce")
        return pd.DataFrame(output, index=frame.index)

    def get_momentum_prices(
        self, tickers: Iterable[str], period: str = "15mo",
    ) -> pd.DataFrame:
        symbols = [self._canonical_ticker(ticker) for ticker in tickers]
        unresolved = [ticker for ticker in symbols if ticker not in self._instrument_by_ticker]
        if unresolved:
            # Reloading the chain also makes direct calls independent from call order.
            self.get_momentum_universe()
        frames: list[pd.DataFrame] = []
        for start in range(0, len(symbols), self.batch_size):
            batch = symbols[start:start + self.batch_size]
            instruments = [self._instrument_by_ticker[ticker] for ticker in batch]
            raw = self._history_matrix(instruments, period, self.momentum_field)
            reverse = {
                instrument: ticker for ticker, instrument in zip(batch, instruments)
            }
            raw = raw.rename(columns=reverse)
            if not raw.empty:
                frames.append(raw)
        if not frames:
            raise RuntimeError("LSEG returned no momentum price history")
        prices = pd.concat(frames, axis=1)
        return prices.loc[:, ~prices.columns.duplicated()].sort_index()

    def get_history(self, ticker: str, period: str = "3mo") -> pd.DataFrame:
        try:
            frame = self._history_matrix([str(ticker)], period)
            if frame.empty:
                raise ValueError("No historical closing prices returned")
            series = pd.to_numeric(frame.iloc[:, 0], errors="coerce").dropna()
            return pd.DataFrame({"Date": series.index, "Price": series.to_numpy()})
        except Exception as exc:
            logger.warning("LSEG historical prices failed for %s: %s", ticker, exc)
            return pd.DataFrame(columns=["Date", "Price"])

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
        instrument = str(asset.get("data_id") or asset.get("ric") or asset["ticker"])
        try:
            history = self._history_matrix([instrument], "1mo")
            closes = pd.to_numeric(history.iloc[:, 0], errors="coerce").dropna()
            if len(closes) < 2:
                raise ValueError("Fewer than two LSEG closing prices returned")
            current, previous = float(closes.iloc[-1]), float(closes.iloc[-2])
            quote.current_price = current
            quote.previous_close = previous
            quote.absolute_change = current - previous
            quote.percentage_change = percentage_change(current, previous)
            quote.timestamp = closes.index[-1].to_pydatetime()
        except Exception as exc:
            quote.error = str(exc)
            logger.warning("LSEG quote failed for %s: %s", instrument, exc)
        return quote

    def get_security_metadata(
        self, tickers: Iterable[str],
    ) -> list[dict[str, str]]:
        symbols = [self._canonical_ticker(ticker) for ticker in tickers]
        unresolved = [ticker for ticker in symbols if ticker not in self._instrument_by_ticker]
        if unresolved:
            self.get_momentum_universe()
        instruments = [self._instrument_by_ticker[ticker] for ticker in symbols]
        frame = self._api().get_data(
            universe=instruments,
            fields=[self.sector_field, self.industry_field],
        )
        if frame is None or frame.empty:
            return [
                {"ticker": ticker, "sector": "Unknown", "industry": "Unknown"}
                for ticker in symbols
            ]
        instrument_column = _find_column(frame, ("instrument", "ric"), 0)
        sector_column = _find_column(frame, ("economicsector", "sector"), 1)
        industry_column = _find_column(frame, ("industry",), 2)
        ticker_by_instrument = {
            instrument: ticker for ticker, instrument in self._instrument_by_ticker.items()
        }
        rows_by_ticker: dict[str, dict[str, str]] = {}
        for _, row in frame.iterrows():
            ticker = ticker_by_instrument.get(str(row.get(instrument_column)).strip())
            if not ticker:
                continue
            sector = row.get(sector_column)
            industry = row.get(industry_column)
            sector_name = "Unknown" if pd.isna(sector) or not sector else str(sector)
            rows_by_ticker[ticker] = {
                "ticker": ticker,
                "sector": LSEG_SECTOR_NAMES.get(sector_name, sector_name),
                "industry": "Unknown" if pd.isna(industry) or not industry else str(industry),
            }
        return [rows_by_ticker.get(ticker, {
            "ticker": ticker, "sector": "Unknown", "industry": "Unknown",
        }) for ticker in symbols]
