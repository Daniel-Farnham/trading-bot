"""Prompt builders for live trading calls.

Call 1: Discovery & screening (daily). Scans news/fundamentals broadly,
        flags opportunities, expands the universe.
Call 3: Decision & execution (weekly + on trigger). Self-sufficient —
        reuses the sim's proven prompt structure from decision_engine.py.
"""
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


def build_call1_prompt(
    themes_md: str,
    holdings_tickers: list[str],
    watchlist_tickers: list[str],
    universe_tickers: list[str],
    world_view_md: str,
    tactical_view_md: str = "",
    prefetched_news: str = "",
    holdings_news: str = "",
    universe_at_cap: bool = False,
    alpaca_portfolio: str = "",
) -> str:
    """Build the Call 1 discovery prompt.

    Pre-fetched news is always included (guaranteed baseline). Claude also has
    Alpaca MCP tools available to dig deeper into stories and discover new
    opportunities beyond what was pre-fetched.
    """
    today = date.today().isoformat()

    holdings_text = ", ".join(holdings_tickers) if holdings_tickers else "(No current holdings)"
    watchlist_text = ", ".join(watchlist_tickers) if watchlist_tickers else "(Empty watchlist)"
    universe_text = ", ".join(universe_tickers) if universe_tickers else "(No universe configured)"

    news_section = ""
    if prefetched_news:
        news_section = f"""
PRE-FETCHED NEWS (overnight/morning headlines — your baseline):
{prefetched_news}
"""
    if holdings_news:
        news_section += f"""
HOLDINGS-SPECIFIC NEWS:
{holdings_news}
"""

    return f"""You are the research analyst for a Druckenmiller-style macro trading bot.
Today is {today}. Your job is DISCOVERY — find what matters and find diamonds in the rough.

You have pre-fetched news headlines below as a baseline. You also have RESEARCH TOOLS
you should actively use to dig deeper:

- search_news(symbols) — search for news on specific tickers or sectors
- get_fundamentals(ticker) — P/E, revenue growth, margins, debt — validate the thesis
- get_price_action(ticker) — current price, 52-week range, recent returns — is it a good entry?
- get_technicals(ticker) — RSI, MACD, OBV, ATR — are technicals supporting entry?
- screen_by_theme(theme) — find stocks related to a theme (e.g. "data center cooling", "nuclear energy")

USE THESE TOOLS AGGRESSIVELY. Don't just read headlines — investigate. If you see a headline
about memory demand, use screen_by_theme("memory") to find all memory stocks, then
get_fundamentals on the most interesting ones. We are looking for max growth — think PLTR
in 2024/2025. Find the next breakout before the crowd.
{news_section}
ACTUAL PORTFOLIO (live from Alpaca — source of truth):
{alpaca_portfolio if alpaca_portfolio else "(Not available)"}

CURRENT HOLDINGS (from memory):
{holdings_text}

CURRENT WATCHLIST (Call 1 flagged as interesting, monitored for triggers):
{watchlist_text}

KNOWN UNIVERSE ({len(universe_tickers)} stocks, max 150):
{universe_text}
{"UNIVERSE AT CAP (150). To add new stocks, you MUST remove stocks with the lowest potential. Remove stocks that no longer align with themes, have poor fundamentals, or lack catalysts. Use universe_removals in your response." if universe_at_cap else ""}

CURRENT THEMES:
{themes_md}

STRUCTURAL WORLD VIEW (12-18 month direction — do NOT update this):
{world_view_md}

TACTICAL VIEW (near-term catalysts — your observation appends here):
{tactical_view_md if tactical_view_md else "(No tactical view yet)"}

YOUR TASKS:
1. MACRO ASSESSMENT — What happened overnight/this morning? 1 paragraph summary of what
   matters for our portfolio and themes. Focus on regime-level changes, not noise.

2. THEME IMPACTS — Are any of our themes strengthening or weakening based on today's news?
   Be specific about what evidence you found.

3. OPPORTUNITY DISCOVERY — Find stocks with huge upside that match our themes.
   Look BEYOND our known universe. If you discover something compelling:
   - Check the fundamentals (revenue growth, margins, P/E)
   - Check recent price action
   - If it's worth tracking, add it to new_universe_additions
   - We are looking for max growth, think PLTR in 2024 / 2025.

   IMPORTANT: Quality over quantity. Do NOT add stocks to the watchlist or universe just
   to fill space. The watchlist should contain only our highest-conviction ideas. Only add
   a new stock if you genuinely believe it has greater upside potential than what's already
   there. If the current watchlist is strong, leave it alone. If you find something better,
   replace the weakest existing entry — don't just keep adding.

4. HOLDINGS ALERTS — Any news or fundamental changes affecting our current positions?
   Earnings surprises, guidance changes, regulatory actions, competitor moves.

5. WATCHLIST ALERTS — Any news affecting stocks we're already watching?

6. EMERGING SIGNALS — News patterns that don't fit existing themes but could become one.

Respond with ONLY valid JSON:
{{
  "macro_assessment": "1 paragraph — what matters today for our portfolio and themes",
  "theme_impacts": [
    {{"theme": "AI Infrastructure", "direction": "strengthening", "evidence": "MSFT raised capex guidance 20%"}}
  ],
  "flagged_tickers_universe": [
    {{"ticker": "NVDA", "reason": "Blackwell shipments ahead of schedule per supplier checks"}}
  ],
  "new_universe_additions": [
    {{"ticker": "VRT", "reason": "Data center cooling leader, 45% YoY revenue growth, aligns with AI Infra theme"}}
  ],
  "universe_removals": [
    {{"ticker": "KSS", "reason": "Retail headwinds, no alignment with current themes, declining fundamentals"}}
  ],
  "holdings_alerts": [
    {{"ticker": "AVGO", "alert": "Earnings beat, raised guidance, VMware integration ahead of schedule"}}
  ],
  "watchlist_alerts": [
    {{"ticker": "CEG", "alert": "Nuclear restart deal with AWS confirmed, 15-year PPA"}}
  ],
  "emerging_signals": [
    {{"signal": "Multiple defense contractors reporting record backlogs", "potential_theme": "Defense Supercycle"}}
  ],
  "tactical_observation": "One-liner observation for today's tactical view (near-term catalysts/risks)"
}}"""


def build_call3_prompt(
    decision_engine,
    sim_date: str,
    memory_context: str,
    world_state: str,
    technicals_summary: str,
    fundamentals_summary: str,
    portfolio_value: float,
    cash: float,
    bot_return_pct: float = 0.0,
    spy_return_pct: float = 0.0,
    review_number: int = 0,
    review_type: str = "weekly",
    trade_count: int = 0,
    options_context: str = "",
    call1_output: dict | None = None,
    candidate_prices: str = "",
    fresh_news: str = "",
) -> str:
    """Build the Call 3 decision prompt.

    Delegates to the existing DecisionEngine._build_prompt() which contains
    the full proven prompt structure from the sim. Prepends a pre-flight
    refresh block (Call 1 discovery, live candidate prices, news since
    last discovery) so Claude reasons from the freshest possible data.

    Args:
        decision_engine: DecisionEngine instance (used for _build_prompt).
        call1_output: Output from today's Call 1, if available.
        candidate_prices: Live-price block for tickers Claude might trade.
        fresh_news: News headlines since the last Call 1 ran.
        All other args: passed through to _build_prompt().
    """
    # Build the core prompt using the proven sim prompt builder
    base_prompt = decision_engine._build_prompt(
        sim_date=sim_date,
        memory_context=memory_context,
        world_state=world_state,
        technicals_summary=technicals_summary,
        fundamentals_summary=fundamentals_summary,
        portfolio_value=portfolio_value,
        cash=cash,
        bot_return_pct=bot_return_pct,
        spy_return_pct=spy_return_pct,
        review_number=review_number,
        review_type=review_type,
        trade_count=trade_count,
        options_context=options_context,
    )

    # Assemble the pre-flight refresh block: discovery → prices → fresh news.
    sections: list[str] = []
    if call1_output:
        sections.append(_format_call1_for_call3(call1_output))
    if candidate_prices:
        sections.append(candidate_prices)
    if fresh_news:
        sections.append(fresh_news)

    if not sections:
        return base_prompt

    refresh_block = "\n\n".join(sections)

    # Insert before PORTFOLIO STATE so the freshest data is right above the
    # decision context Claude reads next.
    insertion_point = "PORTFOLIO STATE:"
    if insertion_point in base_prompt:
        parts = base_prompt.split(insertion_point, 1)
        return parts[0] + refresh_block + "\n\n" + insertion_point + parts[1]

    return refresh_block + "\n\n" + base_prompt


def _format_call1_for_call3(call1_output: dict) -> str:
    """Format Call 1 output as a context section for Call 3."""
    lines = ["TODAY'S DISCOVERY (from Call 1 morning scan):"]

    macro = call1_output.get("macro_assessment", "")
    if macro:
        lines.append(f"\nMacro: {macro}")

    themes = call1_output.get("theme_impacts", [])
    if themes:
        lines.append("\nTheme Impacts:")
        for t in themes:
            lines.append(f"  - {t.get('theme', '?')}: {t.get('direction', '?')} — {t.get('evidence', '')}")

    flagged = call1_output.get("flagged_tickers_universe", [])
    if flagged:
        lines.append("\nFlagged Tickers:")
        for f in flagged:
            lines.append(f"  - {f.get('ticker', '?')}: {f.get('reason', '')}")

    new_adds = call1_output.get("new_universe_additions", [])
    if new_adds:
        lines.append("\nNewly Added to Universe:")
        for a in new_adds:
            lines.append(f"  - {a.get('ticker', '?')}: {a.get('reason', '')}")

    holdings_alerts = call1_output.get("holdings_alerts", [])
    if holdings_alerts:
        lines.append("\nHoldings Alerts:")
        for h in holdings_alerts:
            lines.append(f"  - {h.get('ticker', '?')}: {h.get('alert', '')}")

    watchlist_alerts = call1_output.get("watchlist_alerts", [])
    if watchlist_alerts:
        lines.append("\nWatchlist Alerts:")
        for w in watchlist_alerts:
            lines.append(f"  - {w.get('ticker', '?')}: {w.get('alert', '')}")

    emerging = call1_output.get("emerging_signals", [])
    if emerging:
        lines.append("\nEmerging Signals:")
        for e in emerging:
            lines.append(f"  - {e.get('signal', '')} (potential theme: {e.get('potential_theme', '?')})")

    return "\n".join(lines)
