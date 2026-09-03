from __future__ import annotations

import math
import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from utils.logger import get_logger

logger = get_logger(__name__)

TRADING_DAYS_6M = int(os.getenv("MOMENTUM_TRADING_DAYS_6M", "126"))
TRADING_DAYS_12M = int(os.getenv("MOMENTUM_TRADING_DAYS_12M", "252"))
ANNUALIZATION_DAYS = int(os.getenv("MOMENTUM_ANNUALIZATION_DAYS", "252"))
PRICE_HISTORY_PERIOD = os.getenv("MOMENTUM_PRICE_HISTORY_PERIOD", "15mo")
MIN_VOLATILITY = float(os.getenv("MOMENTUM_MIN_VOLATILITY", "1e-8"))

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"

RANKING_COLUMNS = [
    "ticker", "rank", "return_6m", "return_12m", "vol_6m", "vol_12m",
    "score_6m", "score_12m", "momentum_score",
]

SECTOR_COLORS = {
    "Technology": "#3a7059",
    "Communication Services": "#50b748",
    "Consumer Cyclical": "#8acb72",
    "Consumer Defensive": "#b0d98c",
    "Financial Services": "#2f8f83",
    "Healthcare": "#65a9a1",
    "Industrials": "#7c8f74",
    "Energy": "#d18a00",
    "Basic Materials": "#b79b52",
    "Real Estate": "#9678a8",
    "Utilities": "#6f8eb2",
    "Unknown": "#9aa8a0",
}


def yahoo_ticker(symbol: str) -> str:
    """Normalize exchange symbols to Yahoo Finance's ticker convention."""
    return str(symbol).strip().upper().replace(".", "-")


def merge_universes(
    sp500_tickers: Iterable[str], nasdaq100_tickers: Iterable[str],
) -> list[str]:
    return sorted({
        ticker
        for raw in (*list(sp500_tickers), *list(nasdaq100_tickers))
        if (ticker := yahoo_ticker(raw))
    })


def _tickers_from_html(html: str, accepted_headers: set[str]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        header_cells = table.find_all("th")
        headers = [cell.get_text(" ", strip=True).lower() for cell in header_cells]
        ticker_index = next(
            (index for index, header in enumerate(headers) if header in accepted_headers), None,
        )
        if ticker_index is None:
            continue
        tickers: list[str] = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) > ticker_index:
                value = cells[ticker_index].get_text(" ", strip=True).split("[")[0]
                if value:
                    tickers.append(value)
        if tickers:
            return tickers
    raise ValueError("Could not locate a constituent ticker column")


def get_universe(
    http_get: Callable[..., Any] = requests.get,
) -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 OTP-Morning-Brief/1.0"}
    sp_response = http_get(SP500_URL, headers=headers, timeout=30)
    sp_response.raise_for_status()
    nasdaq_headers = {**headers, "Accept": "application/json"}
    nasdaq_response = http_get(NASDAQ100_URL, headers=nasdaq_headers, timeout=30)
    nasdaq_response.raise_for_status()
    sp500 = _tickers_from_html(sp_response.text, {"symbol"})
    payload = nasdaq_response.json()
    rows = payload.get("data", {}).get("data", {}).get("rows", [])
    nasdaq100 = [row.get("symbol", "") for row in rows if row.get("symbol")]
    if not nasdaq100:
        raise ValueError("Nasdaq API returned no Nasdaq-100 constituents")
    universe = merge_universes(sp500, nasdaq100)
    logger.info(
        "Momentum universe loaded: %d S&P 500, %d Nasdaq-100, %d unique tickers",
        len(sp500), len(nasdaq100), len(universe),
    )
    return universe


def _extract_close_prices(download: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if download is None or download.empty:
        return pd.DataFrame()
    if isinstance(download.columns, pd.MultiIndex):
        if "Close" in download.columns.get_level_values(0):
            close = download["Close"]
        elif "Close" in download.columns.get_level_values(1):
            close = download.xs("Close", axis=1, level=1)
        else:
            return pd.DataFrame()
    elif "Close" in download.columns:
        close = download[["Close"]].copy()
        close.columns = [tickers[0]]
    else:
        return pd.DataFrame()
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])
    close.columns = [yahoo_ticker(str(column)) for column in close.columns]
    return close.apply(pd.to_numeric, errors="coerce")


def download_prices(
    tickers: Iterable[str],
    period: str = PRICE_HISTORY_PERIOD,
    batch_size: int = 100,
    downloader: Callable[..., Any] | None = None,
) -> pd.DataFrame:
    if downloader is None:
        import yfinance as yf
        downloader = yf.download
    symbols = merge_universes(tickers, [])
    frames: list[pd.DataFrame] = []
    for start in range(0, len(symbols), batch_size):
        batch = symbols[start:start + batch_size]
        try:
            result = downloader(
                batch, period=period, auto_adjust=True, progress=False,
                group_by="column", threads=True, timeout=30,
            )
            close = _extract_close_prices(result, batch)
            if not close.empty:
                frames.append(close)
        except Exception as exc:
            logger.warning(
                "Momentum price batch failed for %d tickers: %s", len(batch), exc,
            )
    if not frames:
        raise RuntimeError("Yahoo Finance returned no momentum price history")
    prices = pd.concat(frames, axis=1)
    prices = prices.loc[:, ~prices.columns.duplicated()].sort_index()
    missing = sorted(set(symbols) - set(prices.columns))
    if missing:
        logger.warning(
            "Momentum prices missing for %d tickers: %s",
            len(missing), ", ".join(missing[:40]),
        )
    return prices


def calculate_momentum(
    prices: pd.DataFrame,
    trading_days_6m: int = TRADING_DAYS_6M,
    trading_days_12m: int = TRADING_DAYS_12M,
    annualization_days: int = ANNUALIZATION_DAYS,
    min_volatility: float = MIN_VOLATILITY,
) -> pd.DataFrame:
    """Calculate the requested 6M/12M volatility-adjusted relative momentum."""
    if prices is None or prices.empty:
        return pd.DataFrame(columns=RANKING_COLUMNS)
    records: list[dict[str, float | str]] = []
    excluded: list[str] = []
    for raw_ticker in prices.columns:
        ticker = yahoo_ticker(str(raw_ticker))
        series = pd.to_numeric(prices[raw_ticker], errors="coerce").dropna()
        if len(series) < trading_days_12m + 1:
            excluded.append(ticker)
            continue
        current = float(series.iloc[-1])
        price_6m = float(series.iloc[-(trading_days_6m + 1)])
        price_12m = float(series.iloc[-(trading_days_12m + 1)])
        daily_returns = series.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
        vol_6m = float(daily_returns.tail(trading_days_6m).std() * math.sqrt(annualization_days))
        vol_12m = float(daily_returns.tail(trading_days_12m).std() * math.sqrt(annualization_days))
        values = (current, price_6m, price_12m, vol_6m, vol_12m)
        if (
            not all(math.isfinite(value) for value in values)
            or price_6m <= 0 or price_12m <= 0
            or vol_6m <= min_volatility or vol_12m <= min_volatility
        ):
            excluded.append(ticker)
            continue
        records.append({
            "ticker": ticker,
            "return_6m": current / price_6m - 1.0,
            "return_12m": current / price_12m - 1.0,
            "vol_6m": vol_6m,
            "vol_12m": vol_12m,
        })
    if not records:
        logger.warning("No securities had sufficient valid data for momentum ranking")
        return pd.DataFrame(columns=RANKING_COLUMNS)

    ranking = pd.DataFrame.from_records(records)
    mean_6m = ranking["return_6m"].mean()
    mean_12m = ranking["return_12m"].mean()
    ranking["score_6m"] = (ranking["return_6m"] - mean_6m) / ranking["vol_6m"]
    ranking["score_12m"] = (ranking["return_12m"] - mean_12m) / ranking["vol_12m"]
    ranking["momentum_score"] = (ranking["score_6m"] + ranking["score_12m"]) / 2.0
    ranking = ranking.replace([np.inf, -np.inf], np.nan).dropna(subset=[
        "return_6m", "return_12m", "vol_6m", "vol_12m",
        "score_6m", "score_12m", "momentum_score",
    ])
    ranking = ranking.sort_values(
        ["momentum_score", "ticker"], ascending=[False, True], kind="stable",
    ).reset_index(drop=True)
    ranking.insert(1, "rank", range(1, len(ranking) + 1))
    logger.info(
        "Momentum ranking calculated for %d tickers; excluded %d: %s",
        len(ranking), len(excluded), ", ".join(excluded[:40]) or "none",
    )
    logger.info("Momentum Top 25: %s", ", ".join(ranking.head(25)["ticker"]))
    return ranking[RANKING_COLUMNS]


def get_top_momentum(ranking: pd.DataFrame, n: int = 25) -> pd.DataFrame:
    return ranking.sort_values("rank").head(n).reset_index(drop=True)


def download_security_metadata(
    tickers: Iterable[str],
    info_getter: Callable[[str], dict[str, Any]] | None = None,
    max_workers: int = 6,
) -> list[dict[str, str]]:
    """Retrieve Yahoo sector/industry metadata with bounded concurrency."""
    if info_getter is None:
        import yfinance as yf
        info_getter = lambda ticker: yf.Ticker(ticker).get_info()

    symbols = merge_universes(tickers, [])

    def retrieve(ticker: str) -> dict[str, str]:
        try:
            info = info_getter(ticker) or {}
            return {
                "ticker": ticker,
                "sector": str(info.get("sector") or "Unknown"),
                "industry": str(info.get("industry") or "Unknown"),
            }
        except Exception as exc:
            logger.warning("Yahoo metadata failed for %s: %s", ticker, exc)
            return {"ticker": ticker, "sector": "Unknown", "industry": "Unknown"}

    rows: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(symbols) or 1))) as executor:
        futures = {executor.submit(retrieve, ticker): ticker for ticker in symbols}
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: row["ticker"])
    logger.info("Yahoo sector/industry metadata resolved for %d tickers", len(rows))
    return rows


def compare_sector_distribution(
    current_df: pd.DataFrame, previous_df: pd.DataFrame,
    three_month_df: pd.DataFrame | None = None, n: int = 25,
) -> list[dict[str, float | int | str]]:
    """Return current/1M/3M Top-N sector shares; every available side totals 100%."""
    def counts(frame: pd.DataFrame) -> tuple[dict[str, int], int]:
        top = frame.sort_values("rank").head(n).copy()
        if "sector" not in top.columns:
            top["sector"] = "Unknown"
        top["sector"] = top["sector"].fillna("Unknown").replace("", "Unknown")
        values = top["sector"].astype(str).value_counts().to_dict()
        return values, len(top)

    current_counts, current_total = counts(current_df)
    previous_counts, previous_total = counts(previous_df)
    three_month_counts, three_month_total = counts(
        three_month_df if three_month_df is not None else previous_df,
    )
    sectors = set(current_counts) | set(previous_counts) | set(three_month_counts)
    rows = []
    for sector in sectors:
        current_pct = 100.0 * current_counts.get(sector, 0) / current_total if current_total else 0.0
        previous_pct = 100.0 * previous_counts.get(sector, 0) / previous_total if previous_total else 0.0
        three_month_pct = (
            100.0 * three_month_counts.get(sector, 0) / three_month_total
            if three_month_total else 0.0
        )
        rows.append({
            "sector": sector,
            "current_count": current_counts.get(sector, 0),
            "previous_count": previous_counts.get(sector, 0),
            "three_month_count": three_month_counts.get(sector, 0),
            "current_pct": current_pct,
            "previous_pct": previous_pct,
            "three_month_pct": three_month_pct,
            "change_pp": current_pct - previous_pct,
            "change_3m_pp": current_pct - three_month_pct,
            "color": SECTOR_COLORS.get(sector, "#596579"),
        })
    return sorted(
        rows,
        key=lambda row: (
            -float(row["current_pct"]), -float(row["previous_pct"]),
            -float(row["three_month_pct"]), str(row["sector"]),
        ),
    )
