from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from data.models import MarketQuote


@dataclass(frozen=True, slots=True)
class StopLossSignal:
    quote: MarketQuote
    peak_price: float
    peak_date: datetime
    current_price: float
    drawdown_pct: float
    status: str


def format_price(quote: MarketQuote) -> str:
    value = quote.current_price
    if value is None:
        return "N/A"
    if quote.display_format == "currency":
        return f"${value:,.2f}"
    if quote.display_format == "percent":
        return f"{value:.2f}%"
    if quote.display_format == "fx":
        return f"{value:.4f}"
    return f"{value:,.2f}"


def format_change(quote: MarketQuote) -> str:
    return "N/A" if quote.percentage_change is None else f"{quote.percentage_change:+.2f}%"


def select_portfolio_performers(
    quotes: list[MarketQuote], limit: int = 3
) -> tuple[list[MarketQuote], list[MarketQuote]]:
    available = [quote for quote in quotes if quote.percentage_change is not None]
    best = sorted(available, key=lambda quote: quote.percentage_change or 0, reverse=True)[:limit]
    worst = sorted(available, key=lambda quote: quote.percentage_change or 0)[:limit]
    return best, worst


def select_stop_loss_signals(
    quotes: list[MarketQuote],
    histories: dict[str, Any],
    watch_threshold: float = 10.0,
    stop_threshold: float = 15.0,
) -> list[StopLossSignal]:
    """Classify positions by drawdown from the latest six-month closing-price peak."""
    import pandas as pd

    signals: list[StopLossSignal] = []
    for quote in quotes:
        frame = histories.get(quote.ticker)
        if frame is None or frame.empty or not {"Date", "Price"}.issubset(frame.columns):
            continue
        clean = frame[["Date", "Price"]].copy()
        clean["Date"] = pd.to_datetime(clean["Date"], errors="coerce", utc=True).dt.tz_convert(None)
        clean["Price"] = pd.to_numeric(clean["Price"], errors="coerce")
        clean = clean.dropna().sort_values("Date")
        if clean.empty:
            continue
        cutoff = clean["Date"].max() - pd.DateOffset(months=6)
        window = clean.loc[clean["Date"] >= cutoff]
        if window.empty:
            continue
        current = float(quote.current_price) if quote.current_price is not None else float(window.iloc[-1]["Price"])
        historical_peak = float(window["Price"].max())
        if current >= historical_peak:
            peak_price = current
            peak_date = quote.timestamp or window.iloc[-1]["Date"].to_pydatetime()
        else:
            peak_price = historical_peak
            peak_row = window.loc[window["Price"] == historical_peak].iloc[-1]
            peak_date = peak_row["Date"].to_pydatetime()
        if peak_price <= 0:
            continue
        drawdown = max(0.0, (peak_price - current) / peak_price * 100.0)
        status = "STOP" if drawdown >= stop_threshold else "WATCH" if drawdown >= watch_threshold else ""
        if status:
            signals.append(StopLossSignal(
                quote, peak_price, peak_date, current, drawdown, status,
            ))
    return sorted(
        signals,
        key=lambda signal: (signal.status != "STOP", -signal.drawdown_pct),
    )
