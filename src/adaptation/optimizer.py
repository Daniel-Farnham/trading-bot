from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta

from src.config import CONFIG
from src.storage.database import Database

logger = logging.getLogger(__name__)


class StrategyOptimizer:
    """Uses Claude Code CLI to review trading performance and adjust strategy."""

    def __init__(self, db: Database, max_change_pct: float | None = None):
        self._db = db
        self._max_change_pct = max_change_pct or CONFIG.get(
            "adaptation", {}
        ).get("max_param_change_pct", 0.20)

    def run_daily_review(self) -> dict:
        """Run end-of-day review. Returns parameter adjustments applied."""
        stats = self._db.get_trade_stats()
        since = (datetime.utcnow() - timedelta(days=7)).isoformat()
        recent_trades = self._db.get_trades_since(since)
        current_params = self._db.get_all_params()

        if stats["total"] < 3:
            logger.info("Not enough closed trades for review (%d). Skipping.", stats["total"])
            return {"skipped": True, "reason": "insufficient_trades"}

        prompt = self._build_review_prompt(stats, recent_trades, current_params)
        suggestions = self._call_claude(prompt)

        if not suggestions:
            logger.info("No parameter changes suggested.")
            return {"changes": []}

        changes = self._apply_changes(suggestions, current_params)
        return {"changes": changes}

    def run_simulation_review(self, stats: dict, recent_trades: list[dict], current_params: dict) -> dict:
        """Review for simulation mode — accepts data directly instead of querying DB."""
        if stats["total"] < 3:
            return {"skipped": True, "reason": "insufficient_trades"}

        prompt = self._build_review_prompt(stats, recent_trades, current_params)
        suggestions = self._call_claude(prompt)

        if not suggestions:
            return {"changes": []}

        changes = self._apply_changes(suggestions, current_params)
        return {"changes": changes}

    def _build_review_prompt(
        self, stats: dict, recent_trades: list[dict], current_params: dict
    ) -> str:
        trade_summary = []
        for t in recent_trades[:30]:
            trade_summary.append({
                "ticker": t["ticker"],
                "side": t["side"],
                "entry": t["entry_price"],
                "exit": t.get("exit_price"),
                "pnl": t.get("pnl"),
                "sentiment": t["sentiment_score"],
                "confidence": t["confidence"],
                "status": t["status"],
                "reasoning": t.get("reasoning", "")[:100],
            })

        return f"""You are a quantitative trading strategy advisor. Review this trading bot's recent performance and suggest parameter adjustments.

PERFORMANCE STATS:
- Total closed trades: {stats['total']}
- Wins: {stats['wins']}, Losses: {stats['losses']}
- Win rate: {stats['win_rate']:.1%}
- Average P&L per trade: ${stats['avg_pnl']:.2f}
- Total P&L: ${stats.get('total_pnl', 0):.2f}

RECENT TRADES (last 7 days):
{json.dumps(trade_summary, indent=2)}

CURRENT STRATEGY PARAMETERS:
{json.dumps(current_params, indent=2)}

ADJUSTABLE PARAMETERS:
- sentiment_buy_threshold (currently {current_params.get('sentiment_buy_threshold', 0.6)}): Min sentiment to trigger buy
- sentiment_sell_threshold (currently {current_params.get('sentiment_sell_threshold', -0.4)}): Sentiment below this triggers sell
- rsi_overbought (currently {current_params.get('rsi_overbought', 70)}): RSI above this blocks buys
- atr_stop_loss_multiplier (currently {current_params.get('atr_stop_loss_multiplier', 2.0)}): ATR multiplier for stop-loss distance
- atr_take_profit_multiplier (currently {current_params.get('atr_take_profit_multiplier', 3.0)}): ATR multiplier for take-profit distance
- max_position_pct (currently {current_params.get('max_position_pct', 0.10)}): Max portfolio % per position

Analyze the trades and suggest parameter changes. Consider:
1. If win rate is low, should we raise the sentiment threshold to be more selective?
2. If stopped out often, should we widen stop-losses?
3. If winners are small, should we adjust take-profit targets?
4. Are there patterns in losing trades we should filter out?

Respond with ONLY valid JSON:
{{
  "analysis": "Brief explanation of what you see",
  "changes": [
    {{
      "param": "parameter_name",
      "old_value": 0.6,
      "new_value": 0.65,
      "reason": "Why this change"
    }}
  ]
}}

If no changes needed, return {{"analysis": "...", "changes": []}}"""

    def _call_claude(self, prompt: str) -> list[dict]:
        max_budget = CONFIG.get("adaptation", {}).get("claude_max_budget_usd", 0.50)

        try:
            result = subprocess.run(
                [
                    "claude", "-p", prompt,
                    "--output-format", "text",
                    "--max-budget-usd", str(max_budget),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )

            if result.returncode != 0:
                logger.error("Claude review failed (exit %d): %s", result.returncode, result.stderr)
                return []

            data = json.loads(result.stdout.strip())
            analysis = data.get("analysis", "")
            if analysis:
                logger.info("Claude analysis: %s", analysis)

            return data.get("changes", [])

        except subprocess.TimeoutExpired:
            logger.error("Claude review timed out")
            return []
        except json.JSONDecodeError as e:
            logger.error("Failed to parse Claude response: %s", e)
            return []
        except FileNotFoundError:
            logger.error("Claude CLI not found. Is Claude Code installed?")
            return []

    def _apply_changes(self, suggestions: list[dict], current_params: dict) -> list[dict]:
        applied = []

        for change in suggestions:
            param = change.get("param", "")
            new_value = change.get("new_value")
            reason = change.get("reason", "")

            if not param or new_value is None:
                continue

            new_value = float(new_value)
            old_value = current_params.get(param)

            # If param doesn't exist yet, use the suggested old_value or a default
            if old_value is None:
                old_value = change.get("old_value")
            if old_value is None:
                continue
            old_value = float(old_value)

            # Safety: cap change at max_change_pct
            if old_value != 0:
                change_pct = abs(new_value - old_value) / abs(old_value)
                if change_pct > self._max_change_pct:
                    direction = 1 if new_value > old_value else -1
                    new_value = old_value + (old_value * self._max_change_pct * direction)
                    logger.info(
                        "Capped %s change to %.1f%% (requested %.1f%%)",
                        param, self._max_change_pct * 100, change_pct * 100,
                    )

            self._db.set_param(param, round(new_value, 4), updated_by="claude_review")
            applied.append({
                "param": param,
                "old_value": old_value,
                "new_value": round(new_value, 4),
                "reason": reason,
            })
            logger.info(
                "Parameter updated: %s %.4f -> %.4f (%s)",
                param, old_value, new_value, reason,
            )

        return applied
