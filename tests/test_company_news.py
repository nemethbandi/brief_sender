from datetime import timezone

from data.company_news import YahooCompanyNewsProvider


class FakeTicker:
    def get_news(self, count=10, tab="news"):
        assert count == 50
        assert tab == "all"
        return [{
            "content": {
                "title": "Sandisk announces new storage product",
                "summary": "Retrieved publisher description.",
                "pubDate": "2026-08-24T08:30:00Z",
                "provider": {"displayName": "Example Publisher"},
                "canonicalUrl": {"url": "https://example.com/sandisk-news"},
            }
        }]


def test_ticker_specific_company_news_is_normalized() -> None:
    provider = YahooCompanyNewsProvider(
        {"ticker": "SNDK", "name": "Sandisk"},
        ticker_factory=lambda ticker: FakeTicker(),
    )
    items = provider.fetch_news()
    assert len(items) == 1
    assert items[0].ticker == "SNDK"
    assert items[0].company == "Sandisk"
    assert items[0].publisher == "Example Publisher"
    assert items[0].published_at.tzinfo == timezone.utc
