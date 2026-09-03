from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from data.models import NewsItem
from utils.logger import get_logger

logger = get_logger(__name__)


class YahooCompanyNewsProvider:
    """Retrieve ticker-specific Yahoo news metadata through yfinance."""

    def __init__(self, company: dict[str, Any], count: int = 50,
                 ticker_factory: Callable[[str], Any] | None = None) -> None:
        self.company = company
        self.count = count
        self._ticker_factory = ticker_factory

    @staticmethod
    def _published_at(value: Any) -> datetime | None:
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value, tz=timezone.utc)
            if isinstance(value, str) and value:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (ValueError, TypeError, OSError):
            return None
        return None

    def _parse_item(self, raw: dict[str, Any]) -> NewsItem | None:
        content = raw.get("content") if isinstance(raw.get("content"), dict) else raw
        title = str(content.get("title") or "").strip()
        url_data = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        url = url_data.get("url", "") if isinstance(url_data, dict) else ""
        url = str(url or content.get("link") or raw.get("link") or "").strip()
        published = self._published_at(
            content.get("pubDate") or content.get("providerPublishTime") or raw.get("providerPublishTime")
        )
        if not title or not url or published is None:
            return None
        provider = content.get("provider") or {}
        publisher = provider.get("displayName", "") if isinstance(provider, dict) else str(provider)
        publisher = str(publisher or content.get("publisher") or raw.get("publisher") or "Yahoo Finance")
        return NewsItem(
            title=title,
            url=url,
            publisher=publisher,
            published_at=published,
            ticker=self.company["ticker"],
            company=self.company.get("name") or self.company["ticker"],
            source_type="yahoo_company_news",
            description=content.get("summary") or None,
            trusted_score=7,
        )

    def fetch_news(self) -> list[NewsItem]:
        try:
            if self._ticker_factory is None:
                import yfinance as yf
                ticker_factory = yf.Ticker
            else:
                ticker_factory = self._ticker_factory
            raw_items = ticker_factory(self.company["ticker"]).get_news(count=self.count, tab="all")
            return [item for raw in raw_items if isinstance(raw, dict) and (item := self._parse_item(raw))]
        except Exception as exc:
            logger.warning("Company news failed for %s: %s", self.company.get("ticker"), exc)
            return []


def associate_company_news(items: list[NewsItem], portfolio: list[dict]) -> list[NewsItem]:
    for item in items:
        if item.ticker:
            continue
        haystack = f"{item.title} {item.description or ''}".lower()
        matches: list[tuple[int, dict]] = []
        for company in portfolio:
            keywords = [word for word in company.get("keywords", []) if len(word.strip()) >= 2]
            matched = sum(1 for word in keywords if word.lower() in haystack)
            if matched:
                matches.append((matched, company))
        if matches:
            company = max(matches, key=lambda pair: pair[0])[1]
            item.ticker, item.company = company["ticker"], company.get("name")
    return items
