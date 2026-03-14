"""Research aggregator — builds structured world-state briefs for Claude.

Pulls news from Alpaca's news API (Benzinga) and formats into focused
sections: macro headlines, sector news, portfolio-relevant news, and a
discovery section for emerging opportunities outside the current watchlist.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime

from src.research.news_client import AlpacaNewsClient

logger = logging.getLogger(__name__)

# Noise keywords — skip articles with these in the title
NOISE_KEYWORDS = [
    "celebrity", "kardashian", "nfl", "nba", "mlb", "sports", "entertainment",
    "horoscope", "lottery", "recipe", "weather forecast", "obituary",
]

# Sector keyword mapping for categorisation
SECTOR_KEYWORDS = {
    "AI/Technology": ["ai", "artificial intelligence", "semiconductor", "chip", "data center", "cloud", "software", "nvidia", "gpu"],
    "Energy": ["oil", "gas", "energy", "renewable", "solar", "wind", "nuclear", "opec"],
    "Healthcare": ["pharma", "biotech", "fda", "drug", "healthcare", "medical", "glp-1", "obesity"],
    "Finance": ["bank", "fed", "interest rate", "credit", "lending", "fintech", "jpmorgan", "goldman"],
    "Consumer": ["retail", "consumer", "spending", "inflation", "costco", "walmart", "target"],
}


def _format_range(start_date: str | date, end_date: str | date) -> str:
    def _to_str(d):
        if isinstance(d, (date, datetime)):
            return d.strftime("%b %-d, %Y")
        try:
            return datetime.strptime(str(d)[:10], "%Y-%m-%d").strftime("%b %-d, %Y")
        except ValueError:
            return str(d)
    return f"{_to_str(start_date)} - {_to_str(end_date)}"


def _is_noise(article: dict) -> bool:
    """Check if article is likely noise/irrelevant."""
    title = ((article.get("title") or "") + " " + (article.get("description") or "")).lower()
    return any(kw in title for kw in NOISE_KEYWORDS)


def _filter_articles(articles: list[dict]) -> list[dict]:
    """Filter out noise articles."""
    return [a for a in articles if not _is_noise(a)]


def _format_article(article: dict) -> str:
    title = article.get("title") or "Untitled"
    source = article.get("source") or "Unknown"
    pub_date = article.get("publishedDate") or ""
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


def _categorise_article(article: dict) -> str | None:
    text = (
        ((article.get("title") or "") + " " + (article.get("description") or ""))
        .lower()
    )
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return sector
    return None


def _extract_discovery_tickers(articles: list[dict], known_tickers: set[str]) -> dict[str, list[dict]]:
    """Find tickers appearing frequently in news that aren't in our watchlist/holdings.

    Returns dict of ticker -> list of articles, sorted by frequency.
    """
    ticker_articles: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        for t in (a.get("tickers") or []):
            ticker_upper = t.upper()
            if ticker_upper and ticker_upper not in known_tickers:
                ticker_articles[ticker_upper].append(a)
    # Only surface tickers with 2+ articles (signal, not noise)
    return {t: arts for t, arts in sorted(
        ticker_articles.items(), key=lambda x: -len(x[1])
    ) if len(arts) >= 2}


def build_world_state(
    start_date: str | date,
    end_date: str | date,
    holdings: list[str] | None = None,
    watchlist: list[str] | None = None,
    client: AlpacaNewsClient | None = None,
) -> str:
    """Build a structured world-state brief for Claude.

    Args:
        start_date: Period start for news fetching.
        end_date: Period end for news fetching.
        holdings: List of ticker symbols currently held.
        watchlist: Full watchlist of tickers we're tracking.
        client: Optional AlpacaNewsClient instance.

    Returns:
        Formatted markdown string with macro, sector, portfolio, and discovery news.
    """
    news_client = client or AlpacaNewsClient()
    date_range = _format_range(start_date, end_date)
    sections = []

    known_tickers = set()
    if holdings:
        known_tickers.update(t.upper() for t in holdings)
    if watchlist:
        known_tickers.update(t.upper() for t in watchlist)

    # --- Broad news (macro + sector + discovery) ---
    try:
        all_articles = _filter_articles(
            news_client.get_news(start_date=start_date, end_date=end_date, limit=50)
        )
    except Exception:
        logger.warning("Failed to fetch news")
        all_articles = []

    # Split into macro (no tickers = broad market) and ticker-tagged
    macro_articles = [a for a in all_articles if not (a.get("tickers") or [])]
    ticker_articles = [a for a in all_articles if a.get("tickers")]

    sections.append(f"## Macro Headlines ({date_range})")
    if macro_articles:
        for a in macro_articles[:8]:
            sections.append(_format_article(a))
    else:
        sections.append("- (No macro headlines available)")
    sections.append("")

    # --- Sector news ---
    by_sector: dict[str, list[dict]] = defaultdict(list)
    for a in all_articles:
        sector = _categorise_article(a)
        if sector:
            by_sector[sector].append(a)

    sections.append("## Sector News")
    if by_sector:
        for sector in sorted(by_sector.keys()):
            sections.append(f"### {sector}")
            for a in by_sector[sector][:4]:
                sections.append(_format_article(a))
            sections.append("")
    else:
        sections.append("- (No sector news available)")
        sections.append("")

    # --- Portfolio-relevant news (ticker-specific, high signal) ---
    if holdings:
        try:
            portfolio_articles = _filter_articles(
                news_client.get_ticker_news(
                    tickers=holdings, start_date=start_date, end_date=end_date,
                )
            )
        except Exception:
            logger.warning("Failed to fetch portfolio news")
            portfolio_articles = []

        sections.append("## Portfolio-Relevant News")
        if portfolio_articles:
            for a in portfolio_articles[:10]:
                tickers_str = ", ".join(
                    t.upper() for t in (a.get("tickers") or [])
                    if t.upper() in known_tickers
                )
                prefix = f"- {tickers_str}: " if tickers_str else "- "
                title = (a.get("title") or "Untitled")
                source = (a.get("source") or "Unknown")
                sections.append(f'{prefix}"{title}" ({source})')
        else:
            sections.append("- (No portfolio-relevant news)")
        sections.append("")

    # --- Discovery: trending tickers outside our universe ---
    if ticker_articles:
        discoveries = _extract_discovery_tickers(ticker_articles, known_tickers)
        if discoveries:
            sections.append("## Emerging Opportunities (not in current watchlist)")
            sections.append("*Tickers appearing frequently in this week's financial news:*")
            for ticker, arts in list(discoveries.items())[:5]:
                top_article = arts[0]
                title = (top_article.get("title") or "Untitled")
                source = (top_article.get("source") or "Unknown")
                sections.append(
                    f'- **{ticker}** ({len(arts)} mentions): "{title}" ({source})'
                )
            sections.append("")

    return "\n".join(sections)
