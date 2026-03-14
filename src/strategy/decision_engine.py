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
        portfolio_value: float = 0.0,
        cash: float = 0.0,
    ) -> dict:
        """Run a weekly thesis-driven review via Claude.

        Returns parsed decision dict with keys:
            world_assessment, thesis_updates, new_positions,
            close_positions, reduce_positions, lessons, weekly_summary
        """
        memory_context = self._tm.get_decision_context()
        prompt = self._build_prompt(
            sim_date, memory_context, world_state, technicals_summary,
            portfolio_value, cash,
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
        portfolio_value: float,
        cash: float,
    ) -> str:
        themes = self._tm.get_all_themes()
        if themes:
            themes_text = "\n".join(
                f"{i+1}. {t['name']} [{t['score']}/5] — {t['description']}"
                for i, t in enumerate(themes)
            )
        else:
            themes_text = "(No themes set — propose some based on your research)"

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

MEMORY (your persistent context):
{memory_context}

THIS WEEK'S RESEARCH:
{world_state}

TECHNICAL TIMING DATA:
{technicals_summary if technicals_summary else "(No technical data available)"}

YOUR THEMES (scored 1-5, higher = stronger conviction):
{themes_text}

Themes are informational — they guide your thinking but don't dictate allocations.
You can propose new themes or adjust scores during monthly reviews.
- New themes start at score 3
- Score range: 2-5 (themes at score 1 are auto-removed)
- Max {self._tm._max_themes} themes at a time

STOCK UNIVERSE (pre-screened candidates you can trade):
{universe_text}
You can also trade stocks outside this universe if you discover them through research.

GOAL: Long-term capital growth. Hold for weeks to quarters.
We are patient investors who buy quality companies aligned with macro themes.
We use pullbacks as entry opportunities. We can go long AND short.

RULES:
- Max 15 positions at any time
- Default allocation: 5-8% per position (max 10%)
- Keep at least 20% cash at all times
- Every position MUST have a thesis with explicit invalidation conditions
- When a thesis is invalidated, EXIT immediately
- Wide catastrophic stops (18%) are set automatically — you don't manage them
- Use technicals only for timing hints (e.g. RSI < 40 = good entry)

SHORTING:
You CAN short stocks. If a company faces structural headwinds (e.g. disrupted by AI,
losing market share, secular decline), you can open a SHORT position with direction "SHORT".
Shorts need the same discipline: explicit thesis, invalidation conditions, and allocation.
Good short candidates: companies vulnerable to technological disruption, those with deteriorating
fundamentals, or those in sectors facing structural decline.

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

Respond with ONLY valid JSON:
{{
  "world_assessment": "Brief summary of what matters this week",
  "thesis_updates": [
    {{"ticker": "AVGO", "status": "ACTIVE", "notes": "Q1 confirmed thesis"}}
  ],
  "new_positions": [
    {{
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
    }}
  ],
  "close_positions": [
    {{"ticker": "TSLA", "reason": "EV margin thesis broken by competition"}}
  ],
  "reduce_positions": [
    {{"ticker": "AAPL", "new_allocation_pct": 4, "reason": "China weakness"}}
  ],
  "theme_updates": [
    {{"name": "AI/Automation", "delta": 1, "reason": "Strong earnings across AI sector"}},
    {{"name": "Nuclear Renaissance", "action": "ADD", "description": "Data centers driving nuclear demand", "reason": "Multiple utility deals announced"}}
  ],
  "lessons": ["New lesson if any"],
  "weekly_summary": "Brief narrative for the quarterly summary"
}}

Theme update rules:
- To adjust an existing theme: {{"name": "...", "delta": +1 or -1, "reason": "..."}}
- To add a new theme: {{"name": "...", "action": "ADD", "description": "...", "reason": "..."}}
- Only adjust themes when there's clear evidence from the news. Max ±1 per review.

If no changes needed, return empty arrays. Always include world_assessment and weekly_summary."""

    def _call_claude(self, prompt: str) -> dict | None:
        try:
            result = subprocess.run(
                [
                    "claude", "-p", prompt,
                    "--output-format", "text",
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

        # Add new theses for new positions
        for pos in response.get("new_positions", []):
            ticker = pos.get("ticker", "")
            if not ticker:
                continue
            self._tm.add_thesis(
                ticker=ticker,
                direction=pos.get("direction", "LONG"),
                thesis=pos.get("thesis", ""),
                entry_price=0.0,  # Filled after execution
                target_price=pos.get("target_price", 0.0),
                stop_price=pos.get("stop_price", 0.0),
                timeframe=pos.get("horizon", ""),
                confidence=pos.get("confidence", "medium"),
            )

        # Remove theses for closed positions
        for close in response.get("close_positions", []):
            ticker = close.get("ticker", "")
            if ticker:
                self._tm.update_thesis(ticker, status="CLOSED")

        # Apply theme updates
        for update in response.get("theme_updates", []):
            name = update.get("name", "")
            if not name:
                continue
            if update.get("action") == "ADD":
                desc = update.get("description", "")
                if desc:
                    self._tm.add_theme(name, desc, score=3)
                    logger.info("  Theme added: %s", name)
            else:
                delta = update.get("delta", 0)
                if delta:
                    self._tm.update_theme_score(name, delta)
                    logger.info("  Theme %s: %+d", name, delta)

        # Append lessons
        for lesson in response.get("lessons", []):
            if lesson and lesson.strip():
                self._tm.append_lesson(lesson)

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
            "weekly_summary": "",
        }
