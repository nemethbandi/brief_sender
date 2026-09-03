from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass(slots=True)
class NewsItem:
    title: str
    url: str
    publisher: str
    published_at: datetime
    ticker: str | None = None
    company: str | None = None
    category: str = "Other"
    source_type: str = "rss"
    description: str | None = None
    trusted_score: int = 0
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)
