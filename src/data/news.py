from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from src.config import get_alpaca_keys, CONFIG


ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"


@dataclass
class NewsArticle:
    headline: str
    summary: str
    source: str
    ticker: str
    url: str
    published_at: str


class NewsFeed:
    def __init__(self, api_key: str | None = None, secret_key: str | None = None):
        if api_key and secret_key:
            self._api_key = api_key
            self._secret_key = secret_key
        else:
            self._api_key, self._secret_key = get_alpaca_keys()

        self._headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
        }

    def fetch_news(
        self,
        ticker: str,
        limit: int = 10,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[NewsArticle]:
        if start is None:
            start = datetime.utcnow() - timedelta(hours=24)
        if end is None:
            end = datetime.utcnow()

        params = {
            "symbols": ticker,
            "limit": limit,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sort": "desc",
        }

        response = httpx.get(
            ALPACA_NEWS_URL, headers=self._headers, params=params, timeout=10.0
        )
        response.raise_for_status()
        data = response.json()

        articles = []
        for item in data.get("news", []):
            symbols = item.get("symbols", [])
            article_ticker = ticker if ticker in symbols else (symbols[0] if symbols else ticker)
            articles.append(
                NewsArticle(
                    headline=item.get("headline", ""),
                    summary=item.get("summary", ""),
                    source=item.get("source", "unknown"),
                    ticker=article_ticker,
                    url=item.get("url", ""),
                    published_at=item.get("created_at", ""),
                )
            )
        return articles

    def fetch_news_bulk(
        self,
        tickers: list[str],
        limit_per_ticker: int = 5,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, list[NewsArticle]]:
        results = {}
        for ticker in tickers:
            try:
                articles = self.fetch_news(
                    ticker, limit=limit_per_ticker, start=start, end=end
                )
                results[ticker] = articles
            except Exception:
                results[ticker] = []
        return results
