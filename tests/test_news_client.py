"""Tests for the Alpaca news client."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.research.news_client import AlpacaNewsClient


# Raw Alpaca API response format
ALPACA_ARTICLES = [
    {
        "id": 1,
        "headline": "Fed signals rate cuts later this year",
        "summary": "The Federal Reserve hinted at potential rate cuts.",
        "created_at": "2024-01-03T14:30:00Z",
        "source": "benzinga",
        "symbols": ["SPY"],
    },
    {
        "id": 2,
        "headline": "NVDA reports record data center revenue",
        "summary": "Nvidia beats expectations with AI-driven growth.",
        "created_at": "2024-01-04T09:00:00Z",
        "source": "benzinga",
        "symbols": ["NVDA"],
    },
]


@pytest.fixture
def client():
    with patch("src.research.news_client.get_alpaca_keys", return_value=("test_key", "test_secret")):
        return AlpacaNewsClient()


class TestGetNews:
    @patch("src.research.news_client.requests.Session.get")
    def test_basic_fetch(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"news": ALPACA_ARTICLES, "next_page_token": None}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        articles = client.get_news()
        assert len(articles) == 2
        # Verify normalisation
        assert articles[0]["title"] == "Fed signals rate cuts later this year"
        assert articles[0]["tickers"] == ["SPY"]
        assert articles[0]["publishedDate"] == "2024-01-03T14:30:00Z"

    @patch("src.research.news_client.requests.Session.get")
    def test_with_symbols(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"news": [ALPACA_ARTICLES[1]], "next_page_token": None}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        articles = client.get_news(symbols=["NVDA"])
        assert len(articles) == 1

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["symbols"] == "NVDA"

    @patch("src.research.news_client.requests.Session.get")
    def test_with_date_strings(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"news": ALPACA_ARTICLES, "next_page_token": None}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        client.get_news(start_date="2024-01-01", end_date="2024-01-07")

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["start"] == "2024-01-01"
        assert params["end"] == "2024-01-07"

    @patch("src.research.news_client.requests.Session.get")
    def test_with_date_objects(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"news": [], "next_page_token": None}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        client.get_news(start_date=date(2024, 1, 1), end_date=date(2024, 1, 7))

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["start"] == "2024-01-01"
        assert params["end"] == "2024-01-07"

    @patch("src.research.news_client.requests.Session.get")
    def test_limit_capped_at_50(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"news": [], "next_page_token": None}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        client.get_news(limit=200)

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["limit"] == 50


class TestPagination:
    @patch("src.research.news_client.requests.Session.get")
    def test_paginates_when_needed(self, mock_get, client):
        page1_resp = MagicMock()
        page1_resp.json.return_value = {"news": ALPACA_ARTICLES, "next_page_token": "abc123"}
        page1_resp.raise_for_status.return_value = None

        page2_resp = MagicMock()
        page2_resp.json.return_value = {"news": [ALPACA_ARTICLES[0]], "next_page_token": None}
        page2_resp.raise_for_status.return_value = None

        mock_get.side_effect = [page1_resp, page2_resp]

        articles = client.get_news(limit=100)
        assert len(articles) == 3
        assert mock_get.call_count == 2


class TestErrorHandling:
    @patch("src.research.news_client.requests.Session.get")
    def test_http_error_returns_partial(self, mock_get, client):
        mock_get.side_effect = requests.exceptions.HTTPError("401 Unauthorized")

        # Should not raise, returns empty list
        articles = client.get_news()
        assert articles == []

    @patch("src.research.news_client.requests.Session.get")
    def test_timeout_returns_empty(self, mock_get, client):
        mock_get.side_effect = requests.exceptions.Timeout("Request timed out")

        articles = client.get_news()
        assert articles == []


class TestConvenienceMethods:
    @patch("src.research.news_client.requests.Session.get")
    def test_get_macro_news(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"news": [ALPACA_ARTICLES[0]], "next_page_token": None}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        articles = client.get_macro_news(start_date="2024-01-01", end_date="2024-01-07")
        assert len(articles) == 1

        # Should NOT have symbols param (broad news)
        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert "symbols" not in params

    @patch("src.research.news_client.requests.Session.get")
    def test_get_ticker_news(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"news": [ALPACA_ARTICLES[1]], "next_page_token": None}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        articles = client.get_ticker_news(
            tickers=["NVDA"], start_date="2024-01-01", end_date="2024-01-07",
        )
        assert len(articles) == 1

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["symbols"] == "NVDA"


class TestNormalisation:
    def test_normalises_alpaca_to_expected_format(self):
        normalised = AlpacaNewsClient._normalise(ALPACA_ARTICLES[1])
        assert normalised["title"] == "NVDA reports record data center revenue"
        assert normalised["description"] == "Nvidia beats expectations with AI-driven growth."
        assert normalised["publishedDate"] == "2024-01-04T09:00:00Z"
        assert normalised["source"] == "benzinga"
        assert normalised["tickers"] == ["NVDA"]

    def test_handles_missing_fields(self):
        normalised = AlpacaNewsClient._normalise({})
        assert normalised["title"] == "Untitled"
        assert normalised["description"] == ""
        assert normalised["tickers"] == []
