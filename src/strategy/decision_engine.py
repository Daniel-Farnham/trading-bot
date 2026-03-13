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
        themes: list[dict] | None = None,
    ):
        self._tm = thesis_manager
        self._themes = themes or CONFIG.get("themes", [
            {"name": "AI/Automation", "description": "Companies building or benefiting from AI, robotics, automation"},
            {"name": "Climate Transition", "description": "Clean energy, EVs, sustainability, grid infrastructure"},
            {"name": "Aging Populations", "description": "Healthcare, pharma, medical devices, senior services"},
            {"name": "Wealth Inequality", "description": "Financial services, fintech, discount retail, luxury"},
        ])

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
        themes_text = "\n".join(
            f"{i+1}. {t['name']} — {t['description']}"
            for i, t in enumerate(self._themes)
        )

        holdings = self._tm.get_holdings()
        holdings_count = len(holdings)
        invested_value = sum(h["current_value"] for h in holdings)
        cash_pct = (cash / portfolio_value * 100) if portfolio_value > 0 else 100

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

YOUR THEMES:
{themes_text}

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
6. Any new lessons learned? Be specific and actionable (include trigger conditions).

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
  "lessons": ["New lesson if any"],
  "weekly_summary": "Brief narrative for the quarterly summary"
}}

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
            "lessons": [],
            "weekly_summary": "",
        }
