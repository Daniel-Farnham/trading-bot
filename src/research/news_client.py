"""Alpaca News API client for thesis-driven research.

Fetches news articles from Alpaca's /v1beta1/news endpoint with proper
date filtering. Replaces Tiingo which ignored date params entirely.

All articles are from Benzinga — a financial source — so no source
filtering is needed on the client side.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import requests

from src.config import get_alpaca_keys

logger = logging.getLogger(__name__)


def _format_date(d: str | date | datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    if isinstance(d, date):
        return d.isoformat()
    return str(d)[:10]


class AlpacaNewsClient:
    """HTTP client for Alpaca's historical news endpoint."""

    BASE_URL = "https://data.alpaca.markets/v1beta1/news"

    def __init__(self):
        api_key, secret_key = get_alpaca_keys()
        self._session = requests.Session()
        self._session.headers.update({
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        })

    def get_news(
        self,
        symbols: list[str] | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        limit: int = 50,
        include_content: bool = False,
    ) -> list[dict]:
        """Fetch news articles from Alpaca.

        Returns list of dicts normalised to match the format world_state.py
        expects: title, description, publishedDate, source, tickers.
        """
        params: dict = {
            "limit": min(limit, 50),  # Alpaca max is 50 per page
            "sort": "desc",
        }

        if symbols:
            params["symbols"] = ",".join(s.upper() for s in symbols)
        if start_date:
            params["start"] = _format_date(start_date)
        if end_date:
            params["end"] = _format_date(end_date)
        if include_content:
            params["include_content"] = "true"

        all_articles = []
        pages_fetched = 0
        max_pages = max(1, (limit + 49) // 50)  # Paginate to reach desired limit

        while pages_fetched < max_pages:
            try:
                resp = self._session.get(self.BASE_URL, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except requests.exceptions.RequestException as e:
                logger.error("Alpaca news API error: %s", e)
                break

            raw_articles = data.get("news", [])
            if not raw_articles:
                break

            for a in raw_articles:
                all_articles.append(self._normalise(a))

            next_token = data.get("next_page_token")
            if not next_token or len(all_articles) >= limit:
                break

            params["page_token"] = next_token
            pages_fetched += 1

        logger.debug("Alpaca news returned %d articles", len(all_articles))
        return all_articles[:limit]

    def get_macro_news(
        self,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> list[dict]:
        """Fetch broad market news (no symbol filter)."""
        return self.get_news(start_date=start_date, end_date=end_date, limit=50)

    def get_ticker_news(
        self,
        tickers: list[str],
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> list[dict]:
        """Fetch news for specific tickers."""
        return self.get_news(symbols=tickers, start_date=start_date, end_date=end_date)

    @staticmethod
    def _normalise(article: dict) -> dict:
        """Normalise Alpaca article to the format world_state.py expects."""
        return {
            "title": article.get("headline") or "Untitled",
            "description": article.get("summary") or "",
            "publishedDate": article.get("created_at") or "",
            "source": article.get("source") or "Unknown",
            "tickers": article.get("symbols") or [],
        }
