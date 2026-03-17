"""V3 Decision Engine — Claude makes thesis-driven investment decisions.

The core brain of V3. Assembles memory + world state + technicals into a prompt,
calls Claude via CLI, parses the JSON response, and updates memory files.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime

from src.config import CONFIG
from src.strategy.thesis_manager import ThesisManager

logger = logging.getLogger(__name__)


class DecisionEngine:
    """Orchestrates Claude's weekly investment review."""

    def __init__(
        self,
        thesis_manager: ThesisManager,
    ):
        self._tm = thesis_manager

    def run_weekly_review(
        self,
        sim_date: str,
        world_state: str,
        technicals_summary: str = "",
        fundamentals_summary: str = "",
        portfolio_value: float = 0.0,
        cash: float = 0.0,
        bot_return_pct: float = 0.0,
        spy_return_pct: float = 0.0,
        review_number: int = 0,
        review_type: str = "weekly",
        trade_count: int = 0,
    ) -> dict:
        """Run a weekly thesis-driven review via Claude.

        Returns parsed decision dict with keys:
            world_assessment, thesis_updates, new_positions,
            close_positions, reduce_positions, lessons, weekly_summary,
            lesson_updates, belief_updates, lessons_to_prune
        """
        memory_context = self._tm.get_decision_context()
        prompt = self._build_prompt(
            sim_date, memory_context, world_state, technicals_summary,
            fundamentals_summary, portfolio_value, cash, bot_return_pct,
            spy_return_pct, review_number, review_type, trade_count,
        )

        response = self._call_claude(prompt)
        if not response:
            logger.warning("No response from Claude for review on %s", sim_date)
            return self._empty_response()

        # Update memory files based on Claude's decisions
        self._apply_to_memory(response, sim_date)

        return response

    def _build_prompt(
        self,
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
    ) -> str:
        holdings = self._tm.get_holdings()
        holdings_count = len(holdings)
        invested_value = sum(h["current_value"] for h in holdings)
        cash_pct = (cash / portfolio_value * 100) if portfolio_value > 0 else 100

        # Build universe text for prompt
        universe = CONFIG.get("universe", {})
        universe_lines = []
        for theme, tickers in universe.items():
            theme_label = theme.replace("_", " ").title()
            universe_lines.append(f"  {theme_label}: {', '.join(tickers)}")
        universe_text = "\n".join(universe_lines) if universe_lines else "(No universe configured)"

        # Separate discovery pool label if present
        universe_text = universe_text.replace("Discovery Pool:", "Broader Market:")

        # Theme discovery for first review
        theme_section = self._theme_section_text(review_number)

        # Monthly belief review section
        monthly_section = self._monthly_review_text(review_type)

        # Anti-churning discipline
        discipline_section = self._trade_discipline_text(trade_count)

        # Lesson update task + JSON schema additions
        lesson_update_task = (
            "8. Review existing lessons — if this week's evidence supports an existing lesson, "
            "tell us to increment its score rather than writing a new one. If evidence contradicts "
            "a lesson, tell us to decrement its score."
        )

        # Build JSON schema
        json_schema = self._build_json_schema(review_type, review_number)

        return f"""CRITICAL: You are making decisions on {sim_date}.
You DO NOT know what happens after this date.
Base your decisions ONLY on the news and data provided below.
Do not reference any events after {sim_date}.

You are the Chief Investment Officer of a thesis-driven trading bot.
Your role is to decide WHAT to own based on how the world is changing.

PORTFOLIO STATE:
- Portfolio Value: ${portfolio_value:,.2f}
- Cash: ${cash:,.2f} ({cash_pct:.1f}%)
- Open Positions: {holdings_count}
- Invested: ${invested_value:,.2f}
- Our Return: {bot_return_pct:+.1f}%
- S&P 500 Return: {spy_return_pct:+.1f}%
- vs Benchmark: {bot_return_pct - spy_return_pct:+.1f}%

MEMORY (your persistent context):
{memory_context}

THIS WEEK'S RESEARCH:
{world_state}

TECHNICAL TIMING DATA:
{technicals_summary if technicals_summary else "(No technical data available)"}

FUNDAMENTALS (quarterly financial data — use to validate thesis quality):
{fundamentals_summary if fundamentals_summary else "(No fundamental data available)"}
NOTE: Unprofitable companies are CAPPED at "high" confidence (max 10% allocation).
Only profitable companies can receive "highest" confidence (15%).

{theme_section}

STOCK UNIVERSE (pre-screened candidates you can trade):
{universe_text}
You can also trade stocks outside this universe if you discover them through research.

GOALS:
1. Target 30%+ annualized return — we are concentrated, conviction-driven investors
2. Beat the S&P 500 by 10%+ annually. This requires CONCENTRATED bets, not diversification.
3. In bear markets, capital preservation matters — shorting and cash are valid strategies.
We are Druckenmiller-style macro investors. We identify regime changes, bet big on our
best ideas, and hold winners for months. We use pullbacks as entry opportunities.

POSITION TIERS:
You have TWO types of positions:

SCOUT POSITIONS (low/medium confidence, max 5% / 8%):
  - Testing a thesis. Small, capped bet to see if you're right.
  - These have MECHANICAL stop losses and targets — the system auto-exits at your stated stop/target price daily.
  - Use these when: interesting setup but uncertain timing, or exploring a new theme.

CORE POSITIONS (high/highest confidence, YOU decide the size):
  - Your BEST ideas. The alpha engine. Druckenmiller's "going for the jugular."
  - NO allocation cap — you decide how much to put on. 10%, 20%, 40% — size to conviction.
  - The only limit is the 20% minimum cash reserve. Deploy the rest as you see fit.
  - These have NO mechanical stop and NO mechanical target. Zero. Nothing.
  - YOU are 100% responsible for exits. The system will not save you.
  - If the thesis is intact, HOLD — even through a 20% drawdown. That's the Burry trade.
  - If the thesis breaks, EXIT IMMEDIATELY — don't hope it comes back.
  - Use "high" when thesis + technicals align strongly.
  - Use "highest" when you want MAXIMUM sizing. Requirements:
    * Thesis is crystal clear with an identifiable catalyst (earnings, policy, sector shift)
    * Technicals confirm: above SMA50, MACD bullish, OBV rising (all three)
    * Fundamentals support: profitable company, reasonable valuation, healthy balance sheet
    * Macro regime aligns with the trade direction
    * You would be genuinely surprised if this trade failed
  - UPGRADING: You can upgrade a scout to core by re-submitting it in new_positions with
    higher confidence. The system will automatically widen the stop from mechanical to
    30% catastrophic. Do this when a scout has confirmed your thesis.
  - PYRAMIDING (adding to winners): To add to an existing position, re-submit the ticker
    in new_positions with the TOTAL allocation you want (not the additional amount).
    Example: if you hold NVDA at ~10% and want to go to 25%, submit allocation_pct: 25.
    The system calculates how many additional shares to buy at the current price.
    ONLY pyramid into positions where: thesis is STRENGTHENING, OBV is rising, and you
    have a clear reason why more capital is warranted (earnings beat, catalyst confirmed).
    Never pyramid into a losing position — that's averaging down, not pyramiding.
  - Hold core positions for weeks to months. A 5-10% drawdown on a core position is NORMAL —
    do NOT exit just because the price dipped. Only exit when the THESIS is broken.
  - When a core position is working (thesis strengthening, OBV rising), ADD TO IT rather than
    opening new positions. Your biggest winners should be your biggest positions.

RULES:
- Max 8 positions at any time — prefer 5-6 concentrated bets over 10+ small ones
- Keep at least 20% cash at all times
- Every position MUST have a thesis with explicit invalidation conditions
- When a thesis is invalidated, EXIT immediately regardless of tier
- At each review, evaluate EVERY core position: is the thesis still valid?
  A core position that has lost money but whose thesis is INTACT should be HELD.
  A core position whose thesis is BROKEN should be closed regardless of P&L.
- Scout positions are auto-managed by stops/targets — focus your review energy on core positions
- Use technicals for entry timing. Key exit signals for CORE positions:
  - Thesis invalidated by news, earnings, or policy change
  - Below SMA50 + MACD bearish + OBV falling (triple distribution signal) — thesis likely broken
  - HOWEVER: a price dip with OBV rising is NOT a sell signal — institutions are accumulating
- For scouts: set tight stops (5-10% below entry). You're testing, not committing.

{discipline_section}

DEPLOYMENT PACING:
{self._deployment_pacing_text(review_number, holdings_count)}

SHORTING:
You SHOULD actively consider shorts — especially when we're trailing the S&P or in a
declining market. Shorts can be scout OR core positions:
- Scout shorts: small (3-5%), mechanical stop, quick thesis test
- Core shorts: larger (8-12%), no mechanical stop, hold through volatility if thesis intact
In a bear market, aim for 1-2 core short positions as portfolio hedges.

WATCHING THESES:
If you see a "Watching" section in your theses, these are positions that were stopped out
but where the thesis may still be valid. You can re-enter a watched position by including
it in new_positions — you don't need to rewrite the thesis from scratch, just reference why
you're re-entering at this price. Watching theses auto-expire after 6 reviews if not re-entered.
If you believe a watching thesis is truly dead, close it via close_positions to remove it.

DISCOVERY:
The research section may include "Emerging Opportunities" — tickers getting significant
news coverage that aren't in our current watchlist. If any look compelling and align with
our themes, consider opening a position. Don't force it, but don't ignore it either.

TASKS:
1. Review world events — what's changed this week that matters?
2. Update each active thesis — still valid? stronger? weakening?
3. Should we open any new positions? Check the Emerging Opportunities section for ideas.
4. Should we SHORT any companies facing structural headwinds?
5. Should we close or reduce any positions? (thesis broken?)
6. Theme check: any themes strengthening or weakening? Any new themes emerging from the news?
7. Any new lessons learned? Be specific and actionable (include trigger conditions).
{lesson_update_task}
{monthly_section}

Respond with ONLY valid JSON:
{json_schema}

Theme update rules:
- To adjust an existing theme: {{"name": "...", "delta": +1 or -1, "reason": "..."}}
- To add a new theme: {{"name": "...", "action": "ADD", "description": "...", "reason": "..."}}
- Only adjust themes when there's clear evidence from the news. Max ±1 per review.

If no changes needed, return empty arrays. Always include world_assessment and weekly_summary."""

    def _theme_section_text(self, review_number: int) -> str:
        """Generate the theme section. First review discovers themes from news."""
        if review_number == 1:
            return (
                "THEME DISCOVERY:\n"
                "Based on the news and technical data above, identify 3-4 investment themes you want to pursue.\n"
                "These should reflect the current market environment, not predetermined ideas.\n"
                "Add them via theme_updates with action \"ADD\"."
            )
        return (
            f"THEMES (see Memory section above — scored 1-5, higher = stronger conviction):\n"
            f"Themes are informational — they guide your thinking but don't dictate allocations.\n"
            f"You can propose new themes or adjust scores. New themes start at score 1 and must prove themselves.\n"
            f"If a theme is decremented below 1 it is auto-removed. Max {self._tm._max_themes} themes."
        )

    @staticmethod
    def _monthly_review_text(review_type: str) -> str:
        """Generate monthly belief review section (only for monthly reviews)."""
        if review_type != "monthly":
            return ""
        return """
MONTHLY BELIEF REVIEW:
Review all current lessons and their validity scores.
- Lessons scoring 3+ that share a common principle should be consolidated into a Belief.
- Max 5 beliefs. If adding a new belief, see if it can be merged into an existing one.
- If a recent lesson contradicts an existing Belief, explain why — should we invalidate the belief or discard the lesson?
- Beliefs are rock-solid investment principles. They should have strong conviction behind them.
- Prune lessons that are no longer relevant or have been absorbed into beliefs.

MONTHLY THEME REVIEW:
- Are any themes no longer supported by recent evidence? Consider decrementing them.
- If we are at or near the theme cap, evaluate whether low-scoring themes should be removed to make room for stronger emerging themes.
- Themes at score 1 that have not been reinforced since last month are candidates for removal.

FORWARD OUTLOOK (12-18 months):
Write a brief forward outlook in your weekly_summary. Answer:
- Based on current trends, what will the dominant macro forces be in 12-18 months?
- Which sectors/companies are best positioned for that world?
- Are our core positions aligned with where the world is GOING, not just where it IS?
- What would invalidate this outlook?
This is the most important part of the monthly review. Core positions should be aligned
with your 12-18 month view. If they aren't, either adjust positions or update the outlook."""

    @staticmethod
    def _trade_discipline_text(trade_count: int) -> str:
        """Generate anti-churning trade discipline section."""
        return (
            f"TRADE DISCIPLINE:\n"
            f"You have executed {trade_count} trades so far. If this exceeds 5 trades per month of simulation,\n"
            f"you may be over-trading. Each trade has real costs (stop-loss risk, slippage).\n"
            f"Prefer holding existing positions over opening new ones unless conviction is significantly higher."
        )

    def _build_json_schema(self, review_type: str, review_number: int) -> str:
        """Build the JSON response schema, including monthly additions when applicable."""
        base = """{
  "world_assessment": "Brief summary of what matters this week",
  "thesis_updates": [
    {"ticker": "AVGO", "status": "ACTIVE", "notes": "Q1 confirmed thesis"}
  ],
  "new_positions": [
    {
      "ticker": "CRWD",
      "action": "BUY",
      "allocation_pct": 6,
      "direction": "LONG",
      "thesis": "Full thesis text explaining why we're buying",
      "invalidation": "What would make us sell",
      "target_price": 200.0,
      "stop_price": 120.0,
      "horizon": "3-6 months",
      "confidence": "high",
      "timing_note": "RSI at 32, good entry point"
    },
    {
      "ticker": "T",
      "action": "SHORT",
      "allocation_pct": 5,
      "direction": "SHORT",
      "thesis": "Thesis for why this company is in structural decline",
      "invalidation": "What would make us cover",
      "target_price": 15.0,
      "stop_price": 25.0,
      "horizon": "3-6 months",
      "confidence": "medium",
      "timing_note": "Breaking below support"
    }
  ],
  "close_positions": [
    {"ticker": "TSLA", "reason": "EV margin thesis broken by competition"}
  ],
  "reduce_positions": [
    {"ticker": "AAPL", "new_allocation_pct": 4, "reason": "China weakness"}
  ],
  "theme_updates": [
    {"name": "AI/Automation", "delta": 1, "reason": "Strong earnings across AI sector"},
    {"name": "Nuclear Renaissance", "action": "ADD", "description": "Data centers driving nuclear demand", "reason": "Multiple utility deals announced"}
  ],
  "lessons": ["New lesson if any"],
  "lesson_updates": [
    {"lesson_number": 3, "delta": 1, "reason": "This week confirmed..."},
    {"lesson_number": 7, "delta": -1, "reason": "Evidence contradicts..."}
  ],"""

        # Monthly additions
        if review_type == "monthly":
            base += """
  "belief_updates": [
    {"name": "Never catch falling knives", "action": "ADD", "description": "...", "supporting_lessons": [3, 7]},
    {"name": "Existing belief", "action": "UPDATE", "description": "refined...", "supporting_lessons": [3, 7, 12]},
    {"name": "Old belief", "action": "REMOVE", "reason": "Lesson 14 contradicts..."}
  ],
  "lessons_to_prune": [3, 7],"""

        # Theme discovery for first review
        if review_number == 1:
            # theme_updates already in base schema, no extra needed
            pass

        base += """
  "weekly_summary": "Brief narrative for the quarterly summary"
}"""
        return base

    @staticmethod
    def _deployment_pacing_text(review_number: int, holdings_count: int) -> str:
        """Generate deployment pacing guidance based on how many reviews have occurred."""
        if review_number <= 1:
            return (
                "This is your FIRST review. Open 1-2 SCOUT positions to test the regime.\n"
                "Do NOT open core positions yet — scouts first."
            )
        elif review_number == 2:
            return (
                "SECOND review. If scouts are confirming, upgrade one to CORE.\n"
                "You can add new positions. Start deploying capital — don't sit in cash."
            )
        elif review_number == 3:
            return (
                "THIRD review. You should have a regime read by now.\n"
                "Core positions are appropriate. Deploy with conviction."
            )
        else:
            cash_warning = ""
            if holdings_count <= 3:
                cash_warning = (
                    "\nWARNING: You only have {0} positions. Cash above 40% is underperformance "
                    "unless you're in a confirmed bear market. If you see opportunities, DEPLOY. "
                    "Druckenmiller's edge came from sizing big, not from holding cash."
                ).format(holdings_count)
            return (
                "Deploy capital based on conviction. Your best idea deserves 20-40% of the portfolio.\n"
                "Sitting in cash during a bull market is the biggest risk — you miss the move."
                + cash_warning
            )

    def _call_claude(self, prompt: str) -> dict | None:
        try:
            result = subprocess.run(
                [
                    "claude", "-p", prompt,
                    "--output-format", "text",
                    "--model", "sonnet",
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )

            if result.returncode != 0:
                logger.error(
                    "Claude review failed (exit %d): %s",
                    result.returncode, result.stderr[:300],
                )
                return None

            raw = result.stdout.strip()
            logger.debug("Claude raw response: %s", raw[:500])

            # Strip markdown code fences
            text = raw
            if "```json" in text:
                text = text.split("```json", 1)[1]
                text = text.split("```", 1)[0]
            elif "```" in text:
                text = text.split("```", 1)[1]
                text = text.split("```", 1)[0]
            text = text.strip()

            data = json.loads(text)

            assessment = data.get("world_assessment", "")
            if assessment:
                logger.info("  World Assessment: %s", assessment[:200])

            return data

        except subprocess.TimeoutExpired:
            logger.error("Claude review timed out")
            return None
        except json.JSONDecodeError as e:
            logger.error("Failed to parse Claude response: %s", e)
            return None
        except FileNotFoundError:
            logger.error("Claude CLI not found. Is Claude Code installed?")
            return None

    def _apply_to_memory(self, response: dict, sim_date: str) -> None:
        """Write Claude's decisions back to memory files."""
        # Update existing thesis statuses
        for update in response.get("thesis_updates", []):
            ticker = update.get("ticker", "")
            if not ticker:
                continue
            self._tm.update_thesis(
                ticker,
                status=update.get("status", "active"),
                notes=update.get("notes", ""),
            )

        # Add new theses for new positions (or upgrade existing)
        for pos in response.get("new_positions", []):
            ticker = pos.get("ticker", "")
            if not ticker:
                continue
            # Preserve original entry price if upgrading an existing thesis
            existing = self._tm.get_by_ticker(ticker)
            entry_price = existing.get("entry_price", 0.0) if existing else 0.0
            self._tm.add_thesis(
                ticker=ticker,
                direction=pos.get("direction", "LONG"),
                thesis=pos.get("thesis", ""),
                entry_price=entry_price,
                target_price=pos.get("target_price", 0.0),
                stop_price=pos.get("stop_price", 0.0),
                timeframe=pos.get("horizon", ""),
                confidence=pos.get("confidence", "medium"),
            )

        # Remove theses for closed positions (Claude chose to close = thesis invalidated)
        for close in response.get("close_positions", []):
            ticker = close.get("ticker", "")
            if ticker:
                self._tm.remove_thesis(ticker)
                self._tm.remove_watching(ticker)  # Also remove from watching if present

        # Apply theme updates
        for update in response.get("theme_updates", []):
            name = update.get("name", "")
            if not name:
                continue
            if update.get("action") == "ADD":
                desc = update.get("description", "")
                if desc:
                    self._tm.add_theme(name, desc)
                    logger.info("  Theme added: %s", name)
            else:
                delta = update.get("delta", 0)
                if delta:
                    self._tm.update_theme_score(name, delta)
                    logger.info("  Theme %s: %+d", name, delta)

        # Append new lessons
        for lesson in response.get("lessons", []):
            if lesson and lesson.strip():
                self._tm.append_lesson(lesson)

        # Apply lesson score updates
        for update in response.get("lesson_updates", []):
            lesson_num = update.get("lesson_number")
            delta = update.get("delta", 0)
            if lesson_num is None or delta == 0:
                continue
            if delta > 0:
                self._tm.increment_lesson_score(lesson_num)
                logger.info("  Lesson %d: score +1 (%s)", lesson_num, update.get("reason", ""))
            elif delta < 0:
                self._tm.decrement_lesson_score(lesson_num)
                logger.info("  Lesson %d: score -1 (%s)", lesson_num, update.get("reason", ""))

        # Apply belief updates (monthly reviews)
        for update in response.get("belief_updates", []):
            name = update.get("name", "")
            action = update.get("action", "").upper()
            if not name:
                continue
            if action == "ADD":
                self._tm.add_belief(
                    name,
                    update.get("description", ""),
                    update.get("supporting_lessons", []),
                )
                logger.info("  Belief added: %s", name)
            elif action == "UPDATE":
                self._tm.update_belief(
                    name,
                    description=update.get("description"),
                    supporting_lessons=update.get("supporting_lessons"),
                )
                logger.info("  Belief updated: %s", name)
            elif action == "REMOVE":
                self._tm.remove_belief(name)
                logger.info("  Belief removed: %s (%s)", name, update.get("reason", ""))

        # Prune lessons (monthly reviews)
        for lesson_num in response.get("lessons_to_prune", []):
            if isinstance(lesson_num, int):
                self._tm.remove_lesson(lesson_num)
                logger.info("  Pruned lesson %d", lesson_num)

    @staticmethod
    def _empty_response() -> dict:
        return {
            "world_assessment": "",
            "thesis_updates": [],
            "new_positions": [],
            "close_positions": [],
            "reduce_positions": [],
            "theme_updates": [],
            "lessons": [],
            "lesson_updates": [],
            "belief_updates": [],
            "lessons_to_prune": [],
            "weekly_summary": "",
        }
