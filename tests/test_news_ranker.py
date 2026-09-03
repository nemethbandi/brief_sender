from datetime import timedelta

from data.models import NewsItem
from processing.news_ranker import detect_event, score_item
from utils.helpers import utc_now


def test_portfolio_earnings_story_scores_deterministically() -> None:
    item = NewsItem("Company raises earnings guidance", "https://example.com/a", "Trusted", utc_now() - timedelta(hours=2), ticker="XYZ", trusted_score=10)
    score_item(item, {"very_high":["earnings","guidance"], "high":[]}, related_move=3)
    assert item.score == 90  # 20 recency + 30 portfolio + 20 keyword + 10 move + 10 publisher
    assert item.category == "Earnings"


def test_event_detection() -> None:
    assert detect_event("Board approves new share buyback") == "Capital allocation"
