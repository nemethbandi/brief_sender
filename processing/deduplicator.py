from __future__ import annotations

import re
from difflib import SequenceMatcher

from data.models import NewsItem

STOP_WORDS = {"a","an","and","are","as","at","be","by","for","from","in","is","it","its","of","on","or","that","the","to","with","after","says"}


def normalize_title(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return " ".join(word for word in words if word not in STOP_WORDS)


def title_similarity(left: str, right: str) -> float:
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    tokens_a, tokens_b = set(a.split()), set(b.split())
    jaccard = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))
    return max(jaccard, SequenceMatcher(None, a, b).ratio())


def deduplicate(items: list[NewsItem], threshold: float = 0.80) -> list[NewsItem]:
    kept: list[NewsItem] = []
    for candidate in sorted(items, key=lambda item: (item.score, item.trusted_score), reverse=True):
        if not any(title_similarity(candidate.title, existing.title) >= threshold for existing in kept):
            kept.append(candidate)
    return kept
