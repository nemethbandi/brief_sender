from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class MarketQuote:
    name: str
    ticker: str
    asset_class: str
    current_price: float | None = None
    previous_close: float | None = None
    absolute_change: float | None = None
    percentage_change: float | None = None
    timestamp: datetime | None = None
    display_format: str = "number"
    error: str | None = None
    region: str = "Other"
    threshold_pct: float | None = None
