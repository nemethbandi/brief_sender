from data.models import NewsItem
from processing.deduplicator import deduplicate, title_similarity
from utils.helpers import utc_now


def item(title: str, score: int) -> NewsItem:
    return NewsItem(title, "https://example.com/" + str(score), "Wire", utc_now(), score=score)


def test_similar_titles_are_deduplicated_and_best_kept() -> None:
    high = item("Nvidia raises guidance after strong earnings", 80)
    low = item("Nvidia raises its guidance after strong earnings", 50)
    assert title_similarity(high.title, low.title) >= .8
    assert deduplicate([low, high], .8) == [high]
