from __future__ import annotations

import re

from data.models import NewsItem
from utils.helpers import clean_html_text


def clean_items(items: list[NewsItem]) -> list[NewsItem]:
    result: list[NewsItem] = []
    for item in items:
        item.title = re.sub(r"\s+", " ", clean_html_text(item.title)).strip()
        item.description = re.sub(r"\s+", " ", clean_html_text(item.description or "")).strip() or None
        if item.title and item.url:
            result.append(item)
    return result
