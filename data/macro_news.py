from __future__ import annotations

from data.models import NewsItem


def categorize_macro(items: list[NewsItem], categories: dict[str, list[str]]) -> list[NewsItem]:
    for item in items:
        text = f" {item.title} {item.description or ''} ".lower()
        for category, keywords in categories.items():
            if any(keyword.lower() in text for keyword in keywords):
                item.category = category
                break
    return items
