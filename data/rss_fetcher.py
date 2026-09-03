from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from data.models import NewsItem
from utils.helpers import clean_html_text, utc_now
from utils.logger import get_logger

logger = get_logger(__name__)


class NewsProvider(ABC):
    @abstractmethod
    def fetch_news(self) -> list[NewsItem]: ...


class RSSNewsProvider(NewsProvider):
    def __init__(self, source: dict[str, Any], timeout: int = 12) -> None:
        self.source = source
        self.timeout = timeout

    def _session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers["User-Agent"] = "MorningMarketBrief/1.0 (internal research; contact configured locally)"
        return session

    @staticmethod
    def _date(entry: Any) -> datetime:
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        return datetime(*struct[:6], tzinfo=timezone.utc) if struct else utc_now()

    def fetch_news(self) -> list[NewsItem]:
        try:
            response = self._session().get(self.source["url"], timeout=self.timeout)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            return [NewsItem(
                title=clean_html_text(entry.get("title", "Untitled")), url=entry.get("link", ""),
                publisher=self.source["name"], published_at=self._date(entry), source_type="rss",
                description=clean_html_text(entry.get("summary", "")) or None,
                trusted_score=int(self.source.get("trusted_score", 0)),
            ) for entry in feed.entries if entry.get("link")]
        except Exception as exc:
            logger.warning("RSS source failed (%s): %s", self.source.get("name"), exc)
            return []
