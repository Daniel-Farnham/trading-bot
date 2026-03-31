"""Pre-trade risk scanner — due diligence check before executing new positions.

Fetches recent news for a specific ticker and scans for negative/risk signals.
Runs in Python (no Claude call needed) — produces a short summary line that
gets appended to the position data before Claude's trade is executed.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from src.research.news_client import AlpacaNewsClient

logger = logging.getLogger(__name__)

# Keywords that indicate potential risk — weighted by severity
RISK_KEYWORDS_HIGH = [
    "investigation", "fraud", "lawsuit", "subpoena", "indictment",
    "SEC", "DOJ", "criminal", "scandal", "whistleblower", "resignation",
    "fired", "terminated", "default", "bankruptcy", "delisted",
]

RISK_KEYWORDS_MEDIUM = [
    "downgrade", "cut", "warning", "miss", "disappointing", "decline",
    "weak", "slump", "loss", "regulatory", "antitrust", "probe",
    "Congressional", "hearing", "scrutiny", "concern", "layoff",
    "restructuring", "impairment", "writedown", "recall",
]

RISK_KEYWORDS_LOW = [
    "bearish", "overvalued", "bubble", "risk", "headwind", "pressure",
    "competition", "threat", "uncertainty", "volatile", "downside",
]


def scan_ticker_risk(
    ticker: str,
    as_of: datetime,
    client: AlpacaNewsClient,
    lookback_days: int = 30,
) -> dict:
    """Scan recent news for a ticker and return a risk assessment.

    Returns dict with:
        - risk_level: "low", "medium", "high"
        - negative_count: number of negative articles found
        - total_count: total articles scanned
        - top_risks: list of top 3 risk headlines
        - summary: one-line summary for the prompt
    """
    start = as_of - timedelta(days=lookback_days)
    try:
        articles = client.get_ticker_news(
            tickers=[ticker],
            start_date=start.strftime("%Y-%m-%d"),
            end_date=as_of.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        logger.warning("Risk scan failed for %s: %s", ticker, e)
        return _empty_result(ticker)

    if not articles:
        return _empty_result(ticker)

    # Score each article
    high_risk_articles = []
    medium_risk_articles = []
    low_risk_articles = []

    for article in articles:
        text = f"{article.get('title', '')} {article.get('description', '')}".lower()
        if _matches_keywords(text, RISK_KEYWORDS_HIGH):
            high_risk_articles.append(article.get("title", ""))
        elif _matches_keywords(text, RISK_KEYWORDS_MEDIUM):
            medium_risk_articles.append(article.get("title", ""))
        elif _matches_keywords(text, RISK_KEYWORDS_LOW):
            low_risk_articles.append(article.get("title", ""))

    negative_count = len(high_risk_articles) + len(medium_risk_articles)
    total_count = len(articles)

    # Determine risk level
    if len(high_risk_articles) >= 2 or negative_count >= 5:
        risk_level = "high"
    elif len(high_risk_articles) >= 1 or negative_count >= 3:
        risk_level = "medium"
    else:
        risk_level = "low"

    # Top risk headlines (high priority first)
    top_risks = (high_risk_articles + medium_risk_articles)[:3]

    # Build summary line
    if risk_level == "low":
        summary = f"RISK SCAN {ticker}: Low risk ({negative_count} negative articles in {lookback_days}d)"
    else:
        risk_headlines = "; ".join(h[:60] for h in top_risks)
        summary = (
            f"RISK SCAN {ticker}: {risk_level.upper()} risk "
            f"({negative_count} negative / {total_count} total articles in {lookback_days}d) "
            f"— {risk_headlines}"
        )

    return {
        "ticker": ticker,
        "risk_level": risk_level,
        "negative_count": negative_count,
        "total_count": total_count,
        "top_risks": top_risks,
        "summary": summary,
    }


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    """Check if text contains any of the keywords."""
    for kw in keywords:
        if kw.lower() in text:
            return True
    return False


def _empty_result(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "risk_level": "unknown",
        "negative_count": 0,
        "total_count": 0,
        "top_risks": [],
        "summary": f"RISK SCAN {ticker}: No news data available",
    }
