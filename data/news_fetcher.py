from __future__ import annotations

from datetime import timedelta
from typing import Any

from data.models import NewsItem
from data.rss_fetcher import RSSNewsProvider
from data.company_news import YahooCompanyNewsProvider
from utils.helpers import utc_now


class NewsService:
    def __init__(self, sources: list[dict[str, Any]]) -> None:
        self.sources = sources

    def fetch(self, lookback_hours: int = 24, portfolio: list[dict[str, Any]] | None = None) -> list[NewsItem]:
        cutoff = utc_now() - timedelta(hours=lookback_hours)
        items: list[NewsItem] = []
        for source in self.sources:
            if source.get("enabled") and source.get("type", "").lower() == "rss":
                items.extend(RSSNewsProvider(source).fetch_news())
        for company in portfolio or []:
            if company.get("enabled", True):
                items.extend(YahooCompanyNewsProvider(company).fetch_news())
        return [item for item in items if item.published_at >= cutoff]
