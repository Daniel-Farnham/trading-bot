from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.research.world_state import build_world_state


# Broad news — mix of macro (no tickers) and ticker-tagged articles
BROAD_ARTICLES = [
    {
        "title": "Fed signals rate cuts later this year",
        "description": "The Federal Reserve hinted at potential rate cuts in 2024.",
        "publishedDate": "2024-01-03T14:30:00+00:00",
        "source": "benzinga",
        "tickers": [],
    },
    {
        "title": "US unemployment rises to 3.8%",
        "description": "Labor market shows signs of cooling.",
        "publishedDate": "2024-01-05T10:00:00+00:00",
        "source": "benzinga",
        "tickers": [],
    },
    {
        "title": "NVDA reports record data center revenue",
        "description": "Nvidia beats expectations with AI chip demand.",
        "publishedDate": "2024-01-04T09:00:00+00:00",
        "source": "benzinga",
        "tickers": ["NVDA"],
    },
    {
        "title": "Solar installations hit record high",
        "description": "Renewable energy solar capacity grew 40% in 2023.",
        "publishedDate": "2024-01-04T12:00:00+00:00",
        "source": "benzinga",
        "tickers": [],
    },
]

PORTFOLIO_ARTICLES = [
    {
        "title": "Broadcom raises guidance on AI demand",
        "description": "AVGO sees strong growth in AI networking chips.",
        "publishedDate": "2024-01-06T08:00:00+00:00",
        "source": "benzinga",
        "tickers": ["AVGO"],
    },
]


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_news.return_value = BROAD_ARTICLES
    client.get_ticker_news.return_value = PORTFOLIO_ARTICLES
    return client


class TestBuildWorldState:
    def test_full_brief(self, mock_client):
        result = build_world_state(
            start_date="2024-01-01",
            end_date="2024-01-07",
            holdings=["AVGO", "NVDA"],
            client=mock_client,
        )

        # Macro section (articles with no tickers)
        assert "Macro Headlines" in result
        assert "Fed signals rate cuts" in result

        # Sector section
        assert "Sector News" in result
        assert "AI/Technology" in result
        assert "NVDA reports record" in result

        # Portfolio section
        assert "Portfolio-Relevant News" in result
        assert "Broadcom raises guidance" in result
        assert "AVGO" in result

    def test_no_holdings(self, mock_client):
        result = build_world_state(
            start_date="2024-01-01",
            end_date="2024-01-07",
            client=mock_client,
        )

        assert "Macro Headlines" in result
        assert "Sector News" in result
        assert "Portfolio-Relevant News" not in result

    def test_empty_news(self):
        empty_client = MagicMock()
        empty_client.get_news.return_value = []
        empty_client.get_ticker_news.return_value = []

        result = build_world_state(
            start_date="2024-01-01",
            end_date="2024-01-07",
            holdings=["AAPL"],
            client=empty_client,
        )

        assert "No macro headlines available" in result
        assert "No sector news available" in result
        assert "No portfolio-relevant news" in result

    def test_api_failure_graceful(self):
        failing_client = MagicMock()
        failing_client.get_news.side_effect = Exception("API down")
        failing_client.get_ticker_news.side_effect = Exception("API down")

        result = build_world_state(
            start_date="2024-01-01",
            end_date="2024-01-07",
            holdings=["AAPL"],
            client=failing_client,
        )

        assert "Macro Headlines" in result
        assert "No macro headlines available" in result

    def test_date_range_in_header(self, mock_client):
        result = build_world_state(
            start_date="2024-01-01",
            end_date="2024-01-07",
            client=mock_client,
        )
        assert "Jan" in result

    def test_sector_categorisation(self, mock_client):
        """Verify articles get categorised into correct sectors."""
        result = build_world_state(
            start_date="2024-01-01",
            end_date="2024-01-07",
            client=mock_client,
        )
        assert "AI/Technology" in result
        assert "Energy" in result

    def test_discovery_section(self):
        """Tickers not in watchlist with 2+ mentions should appear in discovery."""
        articles_with_discovery = [
            {"title": "SMCI surges on AI demand", "description": "", "publishedDate": "2024-01-03T00:00:00Z", "source": "benzinga", "tickers": ["SMCI"]},
            {"title": "SMCI beats earnings", "description": "", "publishedDate": "2024-01-04T00:00:00Z", "source": "benzinga", "tickers": ["SMCI"]},
            {"title": "Tech rally continues", "description": "", "publishedDate": "2024-01-05T00:00:00Z", "source": "benzinga", "tickers": []},
        ]
        client = MagicMock()
        client.get_news.return_value = articles_with_discovery
        client.get_ticker_news.return_value = []

        result = build_world_state(
            start_date="2024-01-01",
            end_date="2024-01-07",
            watchlist=["NVDA", "AAPL"],
            client=client,
        )
        assert "Emerging Opportunities" in result
        assert "SMCI" in result
