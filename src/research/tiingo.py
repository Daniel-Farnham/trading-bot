"""Tiingo News API client for thesis-driven research.

Fetches news articles filtered by ticker, tags, and date ranges.
Used by WorldState to build structured briefs for Claude's decisions.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import requests

from src.config import CONFIG, get_tiingo_key

logger = logging.getLogger(__name__)


def _tiingo_cfg(key: str, default):
    return CONFIG.get("tiingo", {}).get(key, default)


def _format_date(d: str | date | datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


class TiingoClient:
    """HTTP client for Tiingo's news endpoint."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._api_key = api_key or get_tiingo_key()
        self._base_url = (
            base_url or _tiingo_cfg("base_url", "https://api.tiingo.com/tiingo")
        ).rstrip("/")
        self._max_articles = _tiingo_cfg("max_articles", 50)
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        })

    def get_news(
        self,
        tickers: list[str] | None = None,
        tags: list[str] | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Fetch news articles from Tiingo.

        Returns list of dicts with: title, description, publishedDate,
        source, tickers, tags.
        """
        params: dict = {"limit": limit or self._max_articles}

        if tickers:
            params["tickers"] = ",".join(t.lower() for t in tickers)
        if tags:
            params["tags"] = ",".join(tags)

        start = _format_date(start_date)
        end = _format_date(end_date)
        if start:
            params["startDate"] = start
        if end:
            params["endDate"] = end

        url = f"{self._base_url}/news"

        try:
            resp = self._session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            articles = resp.json()
            logger.debug("Tiingo returned %d articles", len(articles))
            return articles
        except requests.exceptions.HTTPError as e:
            logger.error("Tiingo API error: %s", e)
            raise
        except requests.exceptions.ConnectionError as e:
            logger.error("Tiingo connection failed: %s", e)
            raise
        except requests.exceptions.Timeout:
            logger.error("Tiingo request timed out")
            raise

    def get_macro_news(
        self,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> list[dict]:
        """Fetch macro/economic news using configured tags."""
        macro_tags = _tiingo_cfg(
            "macro_tags",
            ["economy", "fed", "inflation", "tariffs", "unemployment", "geopolitics"],
        )
        return self.get_news(tags=macro_tags, start_date=start_date, end_date=end_date)

    def get_sector_news(
        self,
        keywords: list[str],
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> list[dict]:
        """Fetch sector-specific news by keyword tags."""
        return self.get_news(tags=keywords, start_date=start_date, end_date=end_date)

    def get_ticker_news(
        self,
        tickers: list[str],
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> list[dict]:
        """Fetch news for specific tickers."""
        return self.get_news(tickers=tickers, start_date=start_date, end_date=end_date)
