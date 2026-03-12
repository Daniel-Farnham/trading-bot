from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from src.data.news import NewsFeed, NewsArticle


MOCK_NEWS_RESPONSE = {
    "news": [
        {
            "headline": "Apple reports record Q4 revenue",
            "summary": "Apple Inc. reported record quarterly revenue of $90B.",
            "source": "reuters",
            "symbols": ["AAPL"],
            "url": "https://example.com/aapl-earnings",
            "created_at": "2025-06-01T14:30:00Z",
        },
        {
            "headline": "Apple expands AI features in iOS",
            "summary": "New AI features coming to iPhone.",
            "source": "bloomberg",
            "symbols": ["AAPL"],
            "url": "https://example.com/aapl-ai",
            "created_at": "2025-06-01T10:00:00Z",
        },
    ]
}


class TestNewsArticle:
    def test_create_article(self):
        article = NewsArticle(
            headline="Test headline",
            summary="Test summary",
            source="reuters",
            ticker="AAPL",
            url="https://example.com",
            published_at="2025-06-01T10:00:00Z",
        )
        assert article.headline == "Test headline"
        assert article.ticker == "AAPL"


class TestNewsFeed:
    @patch("src.data.news.httpx.get")
    def test_fetch_news(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = MOCK_NEWS_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        feed = NewsFeed(api_key="test_key", secret_key="test_secret")
        articles = feed.fetch_news("AAPL", limit=10)

        assert len(articles) == 2
        assert articles[0].headline == "Apple reports record Q4 revenue"
        assert articles[0].ticker == "AAPL"
        assert articles[0].source == "reuters"
        assert articles[1].headline == "Apple expands AI features in iOS"

    @patch("src.data.news.httpx.get")
    def test_fetch_news_sends_correct_headers(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"news": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        feed = NewsFeed(api_key="my_key", secret_key="my_secret")
        feed.fetch_news("AAPL")

        call_kwargs = mock_get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["APCA-API-KEY-ID"] == "my_key"
        assert headers["APCA-API-SECRET-KEY"] == "my_secret"

    @patch("src.data.news.httpx.get")
    def test_fetch_news_empty_response(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"news": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        feed = NewsFeed(api_key="test_key", secret_key="test_secret")
        articles = feed.fetch_news("AAPL")

        assert articles == []

    @patch("src.data.news.httpx.get")
    def test_fetch_news_api_error_raises(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("403 Forbidden")
        mock_get.return_value = mock_response

        feed = NewsFeed(api_key="bad_key", secret_key="bad_secret")
        with pytest.raises(Exception):
            feed.fetch_news("AAPL")

    @patch("src.data.news.httpx.get")
    def test_fetch_news_bulk(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = MOCK_NEWS_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        feed = NewsFeed(api_key="test_key", secret_key="test_secret")
        results = feed.fetch_news_bulk(["AAPL", "MSFT"], limit_per_ticker=5)

        assert "AAPL" in results
        assert "MSFT" in results
        assert len(results["AAPL"]) == 2

    @patch("src.data.news.httpx.get")
    def test_fetch_news_bulk_handles_errors(self, mock_get):
        mock_get.side_effect = Exception("Network error")

        feed = NewsFeed(api_key="test_key", secret_key="test_secret")
        results = feed.fetch_news_bulk(["AAPL", "MSFT"])

        assert results["AAPL"] == []
        assert results["MSFT"] == []

    @patch("src.data.news.httpx.get")
    def test_fetch_news_missing_fields(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "news": [
                {
                    "headline": "Minimal article",
                    "symbols": ["AAPL"],
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        feed = NewsFeed(api_key="test_key", secret_key="test_secret")
        articles = feed.fetch_news("AAPL")

        assert len(articles) == 1
        assert articles[0].headline == "Minimal article"
        assert articles[0].summary == ""
        assert articles[0].source == "unknown"
