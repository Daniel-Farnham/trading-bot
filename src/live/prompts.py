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
) -> str:
    """Build the Call 1 discovery prompt.

    Call 1 has Alpaca MCP tools available — it can autonomously fetch news,
    quotes, bars, and fundamentals. The prompt tells Claude what to look for
    and what context it already has.
    """
    today = date.today().isoformat()

    holdings_text = ", ".join(holdings_tickers) if holdings_tickers else "(No current holdings)"
    watchlist_text = ", ".join(watchlist_tickers) if watchlist_tickers else "(Empty watchlist)"
    universe_text = ", ".join(universe_tickers) if universe_tickers else "(No universe configured)"

    return f"""You are the research analyst for a Druckenmiller-style macro trading bot.
Today is {today}. Your job is DISCOVERY — find what matters and what's changed.

You have access to the Alpaca API tools. Use them to:
1. Fetch overnight and morning news headlines (broad market, not just our universe)
2. Check ticker-specific news for our current holdings and watchlist
3. Look up quotes, price action, or fundamentals for anything interesting you find
4. Dig deeper into stories that match our investment themes

CURRENT HOLDINGS:
{holdings_text}

CURRENT WATCHLIST (Call 1 flagged as interesting, monitored for triggers):
{watchlist_text}

KNOWN UNIVERSE ({len(universe_tickers)} stocks):
{universe_text}

CURRENT THEMES:
{themes_md}

CURRENT WORLD VIEW:
{world_view_md}

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
  "holdings_alerts": [
    {{"ticker": "AVGO", "alert": "Earnings beat, raised guidance, VMware integration ahead of schedule"}}
  ],
  "watchlist_alerts": [
    {{"ticker": "CEG", "alert": "Nuclear restart deal with AWS confirmed, 15-year PPA"}}
  ],
  "emerging_signals": [
    {{"signal": "Multiple defense contractors reporting record backlogs", "potential_theme": "Defense Supercycle"}}
  ],
  "world_view_observation": "One-liner for today's world_view.md entry"
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
) -> str:
    """Build the Call 3 decision prompt.

    Delegates to the existing DecisionEngine._build_prompt() which contains
    the full proven prompt structure from the sim. Prepends Call 1 output
    as a "TODAY'S DISCOVERY" section if available.

    Args:
        decision_engine: DecisionEngine instance (used for _build_prompt).
        call1_output: Output from today's Call 1, if available.
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

    if not call1_output:
        return base_prompt

    # Prepend Call 1 discovery context
    discovery_section = _format_call1_for_call3(call1_output)

    # Insert after the date/role preamble, before PORTFOLIO STATE
    insertion_point = "PORTFOLIO STATE:"
    if insertion_point in base_prompt:
        parts = base_prompt.split(insertion_point, 1)
        return parts[0] + discovery_section + "\n\n" + insertion_point + parts[1]

    # Fallback: prepend
    return discovery_section + "\n\n" + base_prompt


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
