from __future__ import annotations

import html
import re
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


def safe_url(url: str) -> str:
    parsed = urlparse(url.strip())
    return url.strip() if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def clean_html_text(value: str) -> str:
    from bs4 import BeautifulSoup
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def utc_now() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)


def local_time(value: datetime, timezone_name: str = "Europe/Budapest") -> datetime:
    if value.tzinfo is None:
        from datetime import timezone
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(timezone_name))


def normalize_ticker(value: str) -> str:
    ticker = re.sub(r"[^A-Z0-9.=-]", "", value.upper().strip())
    if not ticker or len(ticker) > 20:
        raise ValueError("Ticker must contain 1-20 letters, numbers, '.', '=', or '-'.")
    return ticker
