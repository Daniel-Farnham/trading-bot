from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.research.tiingo import TiingoClient


SAMPLE_ARTICLES = [
    {
        "title": "Fed signals rate cuts later this year",
        "description": "The Federal Reserve hinted at potential rate cuts.",
        "publishedDate": "2024-01-03T14:30:00+00:00",
        "source": "Reuters",
        "tickers": ["spy"],
        "tags": ["economy", "fed"],
    },
    {
        "title": "NVDA reports record data center revenue",
        "description": "Nvidia beats expectations with AI-driven growth.",
        "publishedDate": "2024-01-04T09:00:00+00:00",
        "source": "CNBC",
        "tickers": ["nvda"],
        "tags": ["technology", "earnings"],
    },
]


@pytest.fixture
def client():
    return TiingoClient(api_key="test_key_123")


class TestGetNews:
    @patch("src.research.tiingo.requests.Session.get")
    def test_basic_fetch(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_ARTICLES
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        articles = client.get_news()
        assert len(articles) == 2
        assert articles[0]["title"] == "Fed signals rate cuts later this year"
        mock_get.assert_called_once()

    @patch("src.research.tiingo.requests.Session.get")
    def test_with_tickers(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [SAMPLE_ARTICLES[1]]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        articles = client.get_news(tickers=["NVDA"])
        assert len(articles) == 1

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["tickers"] == "nvda"

    @patch("src.research.tiingo.requests.Session.get")
    def test_with_date_strings(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_ARTICLES
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        client.get_news(start_date="2024-01-01", end_date="2024-01-07")

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["startDate"] == "2024-01-01"
        assert params["endDate"] == "2024-01-07"

    @patch("src.research.tiingo.requests.Session.get")
    def test_with_date_objects(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        client.get_news(start_date=date(2024, 1, 1), end_date=date(2024, 1, 7))

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["startDate"] == "2024-01-01"
        assert params["endDate"] == "2024-01-07"

    @patch("src.research.tiingo.requests.Session.get")
    def test_with_tags(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [SAMPLE_ARTICLES[0]]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        client.get_news(tags=["economy", "fed"])

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["tags"] == "economy,fed"

    @patch("src.research.tiingo.requests.Session.get")
    def test_custom_limit(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        client.get_news(limit=10)

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["limit"] == 10


class TestErrorHandling:
    @patch("src.research.tiingo.requests.Session.get")
    def test_http_error(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
        mock_get.return_value = mock_resp

        with pytest.raises(requests.exceptions.HTTPError):
            client.get_news()

    @patch("src.research.tiingo.requests.Session.get")
    def test_connection_error(self, mock_get, client):
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        with pytest.raises(requests.exceptions.ConnectionError):
            client.get_news()

    @patch("src.research.tiingo.requests.Session.get")
    def test_timeout(self, mock_get, client):
        mock_get.side_effect = requests.exceptions.Timeout("Request timed out")

        with pytest.raises(requests.exceptions.Timeout):
            client.get_news()


class TestConvenienceMethods:
    @patch("src.research.tiingo.requests.Session.get")
    def test_get_macro_news(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [SAMPLE_ARTICLES[0]]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        articles = client.get_macro_news(start_date="2024-01-01", end_date="2024-01-07")
        assert len(articles) == 1

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert "economy" in params["tags"]

    @patch("src.research.tiingo.requests.Session.get")
    def test_get_sector_news(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_ARTICLES
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        articles = client.get_sector_news(
            keywords=["technology", "ai"],
            start_date="2024-01-01", end_date="2024-01-07",
        )
        assert len(articles) == 2

    @patch("src.research.tiingo.requests.Session.get")
    def test_get_ticker_news(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [SAMPLE_ARTICLES[1]]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        articles = client.get_ticker_news(
            tickers=["NVDA"], start_date="2024-01-01", end_date="2024-01-07",
        )
        assert len(articles) == 1

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["tickers"] == "nvda"
