from __future__ import annotations

from data.models import NewsItem
from utils.helpers import utc_now

EVENTS = {
    "Earnings": ["earnings", "results", "revenue", "eps"], "Guidance": ["guidance", "outlook", "profit warning"],
    "M&A": ["acquisition", "merger", "takeover", "acquire"], "Management": ["ceo", "cfo", "resigns", "appoints"],
    "Capital allocation": ["dividend", "buyback", "capital raise"], "Analyst action": ["upgrade", "downgrade", "price target"],
    "Regulation": ["regulation", "regulator", "sec investigation", "antitrust"], "Legal": ["lawsuit", "fraud", "legal"],
    "Product": ["product launch", "launches", "unveils"], "Contract": ["contract", "partnership"],
}


def detect_event(text: str) -> str:
    lowered = text.lower()
    return next((category for category, terms in EVENTS.items() if any(term in lowered for term in terms)), "Other")


def score_item(item: NewsItem, keywords: dict, related_move: float | None = None) -> NewsItem:
    age = max(0.0, (utc_now() - item.published_at).total_seconds() / 3600)
    recency = 20 if age <= 3 else 15 if age <= 6 else 10 if age <= 12 else 5 if age <= 24 else 0
    score, reasons = float(recency), [f"recency +{recency}"] if recency else []
    if item.ticker:
        score += 30; reasons.append("portfolio +30")
    text = f"{item.title} {item.description or ''}".lower()
    very_high = [word for word in keywords.get("very_high", []) if word.lower() in text]
    high = [word for word in keywords.get("high", []) if word.lower() in text]
    if very_high:
        score += 20; reasons.append("very-high keyword +20")
    elif high:
        score += 10; reasons.append("high keyword +10")
    if related_move is not None and abs(related_move) >= 2:
        score += 10; reasons.append("large related move +10")
    score += item.trusted_score
    if item.trusted_score:
        reasons.append(f"publisher +{item.trusted_score}")
    item.score, item.score_reasons = score, reasons
    if item.ticker:
        item.category = detect_event(text)
    return item


def rank_news(items: list[NewsItem], keywords: dict, moves: dict[str, float] | None = None) -> list[NewsItem]:
    moves = moves or {}
    return sorted((score_item(item, keywords, moves.get(item.ticker or "")) for item in items), key=lambda item: (item.score, item.published_at), reverse=True)
