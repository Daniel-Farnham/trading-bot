"""Research aggregator — builds structured world-state briefs for Claude.

Pulls news from Tiingo and formats it into sections:
macro headlines, sector news, and portfolio-relevant news.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime

from src.research.tiingo import TiingoClient

logger = logging.getLogger(__name__)


def _format_range(start_date: str | date, end_date: str | date) -> str:
    """Format a date range for display headers."""
    def _to_str(d):
        if isinstance(d, (date, datetime)):
            return d.strftime("%b %-d, %Y")
        # Parse string date
        try:
            return datetime.strptime(str(d)[:10], "%Y-%m-%d").strftime("%b %-d, %Y")
        except ValueError:
            return str(d)
    return f"{_to_str(start_date)} - {_to_str(end_date)}"


def _format_article(article: dict) -> str:
    """Format a single article as a bullet point."""
    title = article.get("title", "Untitled")
    source = article.get("source", "Unknown")
    pub_date = article.get("publishedDate", "")
    if pub_date:
        try:
            dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
            date_str = dt.strftime("%b %-d")
        except (ValueError, AttributeError):
            date_str = pub_date[:10]
    else:
        date_str = ""

    suffix = f" ({source}, {date_str})" if date_str else f" ({source})"
    return f'- "{title}"{suffix}'


# Sector keyword mapping for categorisation
SECTOR_KEYWORDS = {
    "AI/Technology": ["ai", "artificial intelligence", "semiconductor", "chip", "data center", "cloud", "software"],
    "Energy": ["oil", "gas", "energy", "renewable", "solar", "wind", "nuclear"],
    "Healthcare": ["pharma", "biotech", "fda", "drug", "healthcare", "medical"],
    "Finance": ["bank", "fed", "interest rate", "credit", "lending", "fintech"],
}


def _categorise_article(article: dict) -> str | None:
    """Assign a sector to an article based on title/description keywords."""
    text = (
        (article.get("title", "") + " " + article.get("description", ""))
        .lower()
    )
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return sector
    return None


def build_world_state(
    start_date: str | date,
    end_date: str | date,
    holdings: list[str] | None = None,
    client: TiingoClient | None = None,
) -> str:
    """Build a structured world-state brief for Claude.

    Args:
        start_date: Period start for news fetching.
        end_date: Period end for news fetching.
        holdings: List of ticker symbols currently held (for portfolio-relevant section).
        client: Optional TiingoClient instance (created if not provided).

    Returns:
        Formatted markdown string with macro, sector, and portfolio news.
    """
    tiingo = client or TiingoClient()
    date_range = _format_range(start_date, end_date)
    sections = []

    # --- Macro headlines ---
    try:
        macro_articles = tiingo.get_macro_news(start_date=start_date, end_date=end_date)
    except Exception:
        logger.warning("Failed to fetch macro news")
        macro_articles = []

    sections.append(f"## Macro Headlines ({date_range})")
    if macro_articles:
        for a in macro_articles[:10]:
            sections.append(_format_article(a))
    else:
        sections.append("- (No macro headlines available)")
    sections.append("")

    # --- Sector news ---
    try:
        all_sector_articles = tiingo.get_news(start_date=start_date, end_date=end_date, limit=100)
    except Exception:
        logger.warning("Failed to fetch sector news")
        all_sector_articles = []

    by_sector: dict[str, list[dict]] = defaultdict(list)
    for a in all_sector_articles:
        sector = _categorise_article(a)
        if sector:
            by_sector[sector].append(a)

    sections.append("## Sector News")
    if by_sector:
        for sector in sorted(by_sector.keys()):
            sections.append(f"### {sector}")
            for a in by_sector[sector][:5]:
                sections.append(_format_article(a))
            sections.append("")
    else:
        sections.append("- (No sector news available)")
        sections.append("")

    # --- Portfolio-relevant news ---
    if holdings:
        try:
            portfolio_articles = tiingo.get_ticker_news(
                tickers=holdings, start_date=start_date, end_date=end_date,
            )
        except Exception:
            logger.warning("Failed to fetch portfolio news")
            portfolio_articles = []

        sections.append("## Portfolio-Relevant News")
        if portfolio_articles:
            for a in portfolio_articles[:10]:
                tickers_str = ", ".join(
                    t.upper() for t in (a.get("tickers", []) or [])
                    if t.upper() in [h.upper() for h in holdings]
                )
                prefix = f"- {tickers_str}: " if tickers_str else "- "
                title = a.get("title", "Untitled")
                source = a.get("source", "Unknown")
                sections.append(f'{prefix}"{title}" ({source})')
        else:
            sections.append("- (No portfolio-relevant news)")
        sections.append("")

    return "\n".join(sections)
