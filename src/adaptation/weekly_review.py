"""Weekly Strategic Review — the "CEO" layer.

Uses claude -p (with Alpaca MCP when available) to perform a comprehensive
strategic review: evaluate themes, discover new stocks, manage watchlist,
and set direction for the coming week.
"""
from __future__ import annotations

import json
import logging
import subprocess
from collections import defaultdict

from src.adaptation.journal import StrategyJournal
from src.config import CONFIG
from src.data.watchlist import Watchlist
from src.storage.database import Database

logger = logging.getLogger(__name__)


class WeeklyReview:
    """Performs weekly strategic reviews via Claude CLI."""

    def __init__(
        self,
        db: Database,
        journal: StrategyJournal,
        watchlist: Watchlist,
    ):
        self._db = db
        self._journal = journal
        self._watchlist = watchlist
        self._max_adds = CONFIG.get("adaptation", {}).get("weekly_max_watchlist_adds", 5)
        self._max_removes = CONFIG.get("adaptation", {}).get("weekly_max_watchlist_removes", 3)

    def run(
        self,
        portfolio_value: float,
        cash: float,
        positions_count: int,
        closed_trades: list[dict],
        initial_cash: float = 100000.0,
        date_str: str = "",
    ) -> dict:
        """Run the weekly strategic review. Returns changes applied."""
        stats = self._db.get_trade_stats()
        current_params = self._db.get_all_params()

        # Build per-ticker performance
        ticker_performance = self._calc_ticker_performance(closed_trades)

        prompt = self._build_prompt(
            stats, current_params, ticker_performance,
            portfolio_value, cash, positions_count, initial_cash,
        )

        response = self._call_claude(prompt)
        if not response:
            return {"changes": [], "watchlist_changes": []}

        # Process watchlist changes
        watchlist_changes = self._apply_watchlist_changes(response)

        # Process parameter changes
        param_changes = response.get("parameter_changes", [])
        applied_params = self._apply_param_changes(param_changes, current_params)

        analysis = response.get("market_analysis", "")
        weekly_direction = response.get("weekly_direction", "")

        # Write journal entry
        total_return_pct = ((portfolio_value / initial_cash) - 1) * 100 if initial_cash > 0 else 0
        all_changes = applied_params + [
            {"param": f"watchlist_{c['action']}", "old_value": "", "new_value": c["ticker"], "reason": c.get("reason", "")}
            for c in watchlist_changes
        ]

        tracking = ""
        if weekly_direction:
            tracking = f"Weekly direction: {weekly_direction}"

        self._journal.append_entry(
            date=date_str,
            review_type="weekly",
            portfolio_value=portfolio_value,
            total_return_pct=total_return_pct,
            cash=cash,
            positions_count=positions_count,
            trades_total=stats.get("total", 0),
            win_rate=stats.get("win_rate", 0) * 100,
            changes=all_changes,
            analysis=analysis,
            tracking_notes=tracking,
        )

        return {
            "changes": applied_params,
            "watchlist_changes": watchlist_changes,
            "analysis": analysis,
            "weekly_direction": weekly_direction,
        }

    def _calc_ticker_performance(self, closed_trades: list[dict]) -> dict:
        """Calculate per-ticker performance from closed trades."""
        by_ticker = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        for t in closed_trades:
            tk = t.get("ticker", "")
            by_ticker[tk]["trades"] += 1
            by_ticker[tk]["pnl"] += t.get("pnl", 0)
            if t.get("pnl", 0) > 0:
                by_ticker[tk]["wins"] += 1
        return dict(by_ticker)

    def _build_prompt(
        self,
        stats: dict,
        current_params: dict,
        ticker_performance: dict,
        portfolio_value: float,
        cash: float,
        positions_count: int,
        initial_cash: float,
    ) -> str:
        journal_context = self._journal.get_recent_context(max_entries=10)
        total_return = ((portfolio_value / initial_cash) - 1) * 100 if initial_cash > 0 else 0

        perf_lines = []
        for tk, s in sorted(ticker_performance.items(), key=lambda x: -x[1]["pnl"]):
            wr = s["wins"] / s["trades"] * 100 if s["trades"] > 0 else 0
            perf_lines.append(f"  {tk}: {s['trades']} trades, {wr:.0f}% win rate, ${s['pnl']:+.2f} P&L")

        return f"""You are the Chief Investment Officer of an autonomous trading bot.
Perform a WEEKLY STRATEGIC REVIEW.

STRATEGY JOURNAL (your previous decisions and outcomes):
{journal_context}

---

PORTFOLIO STATE:
- Portfolio value: ${portfolio_value:,.2f} ({total_return:+.1f}% return)
- Cash: ${cash:,.2f}
- Open positions: {positions_count}
- Total trades: {stats.get('total', 0)}
- Win rate: {stats.get('win_rate', 0):.1%}
- Total P&L: ${stats.get('total_pnl', 0):,.2f}

CURRENT WATCHLIST:
{json.dumps(self._watchlist.symbols, indent=2)}

PER-TICKER PERFORMANCE:
{chr(10).join(perf_lines) if perf_lines else '  (no closed trades yet)'}

CURRENT STRATEGY PARAMETERS:
{json.dumps(current_params, indent=2)}

OUR MACRO THEMES:
1. AI/Automation — companies building or benefiting from AI
2. Climate Transition — clean energy, EVs, sustainability
3. Aging Populations — healthcare, pharma, medical devices
4. Wealth Inequality — financial services, affordable goods

INVESTMENT GOAL: 20%+ annual returns, high risk tolerance, swing trading.
We can go long AND short.

AVAILABLE TOOLS:
You have access to Alpaca MCP tools. USE THEM to research stocks before making decisions:
- Look up current market data, stock prices, and fundamentals
- Research potential new watchlist additions — check their recent performance and news
- Verify that any stocks you suggest are real, US-listed, and actively trading
- Check our current positions and market conditions

YOUR TASKS:
1. Use Alpaca tools to research current market conditions
2. Analyze how the portfolio is performing against our 20% annual target
3. Evaluate which stocks are working and which aren't
4. Research and suggest up to {self._max_adds} new stocks to ADD to the watchlist (use Alpaca to verify they exist and check their fundamentals)
5. Suggest up to {self._max_removes} underperforming stocks to REMOVE from the watchlist
6. Suggest any parameter changes
7. Set strategic direction for the coming week

Respond with ONLY valid JSON:
{{
  "market_analysis": "Brief analysis of market conditions and portfolio performance",
  "watchlist_adds": [
    {{"ticker": "AVGO", "reason": "AI chip demand growing, strong momentum"}}
  ],
  "watchlist_removes": [
    {{"ticker": "XOM", "reason": "No theme alignment, consistently losing"}}
  ],
  "parameter_changes": [
    {{"param": "sentiment_buy_threshold", "old_value": 0.6, "new_value": 0.65, "reason": "..."}}
  ],
  "weekly_direction": "Brief strategic direction for next week"
}}

If no changes needed, return empty arrays for each field."""

    def _call_claude(self, prompt: str) -> dict | None:
        max_budget = CONFIG.get("adaptation", {}).get("weekly_max_budget_usd", 1.00)

        try:
            result = subprocess.run(
                [
                    "claude", "-p", prompt,
                    "--output-format", "text",
                    "--max-budget-usd", str(max_budget),
                ],
                capture_output=True,
                text=True,
                timeout=600,  # Weekly reviews use MCP tools, need more time
            )

            if result.returncode != 0:
                logger.error("Weekly review failed (exit %d): %s", result.returncode, result.stderr)
                return None

            raw = result.stdout.strip()
            logger.debug("Weekly review raw response: %s", raw[:500])

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
            analysis = data.get("market_analysis", "")
            if analysis:
                logger.info("Weekly review analysis: %s", analysis)

            return data

        except subprocess.TimeoutExpired:
            logger.error("Weekly review timed out")
            return None
        except json.JSONDecodeError as e:
            logger.error("Failed to parse weekly review response: %s — raw: %s", e, raw[:300])
            return None
        except FileNotFoundError:
            logger.error("Claude CLI not found. Is Claude Code installed?")
            return None

    def _apply_watchlist_changes(self, response: dict) -> list[dict]:
        """Apply watchlist add/remove suggestions."""
        changes = []

        # Adds
        for add in response.get("watchlist_adds", [])[:self._max_adds]:
            ticker = add.get("ticker", "").upper().strip()
            if not ticker:
                continue
            if ticker in self._watchlist.symbols:
                continue
            self._watchlist.add(ticker)
            changes.append({"action": "add", "ticker": ticker, "reason": add.get("reason", "")})
            logger.info("Watchlist ADD: %s (%s)", ticker, add.get("reason", ""))

        # Removes — don't go below 5 stocks
        min_size = 5
        for remove in response.get("watchlist_removes", [])[:self._max_removes]:
            ticker = remove.get("ticker", "").upper().strip()
            if not ticker:
                continue
            if ticker not in self._watchlist.symbols:
                continue
            if len(self._watchlist.symbols) <= min_size:
                logger.info("Skipping removal of %s — watchlist at minimum size", ticker)
                continue
            self._watchlist.remove(ticker)
            changes.append({"action": "remove", "ticker": ticker, "reason": remove.get("reason", "")})
            logger.info("Watchlist REMOVE: %s (%s)", ticker, remove.get("reason", ""))

        return changes

    def _apply_param_changes(self, suggestions: list[dict], current_params: dict) -> list[dict]:
        """Apply parameter changes with safety cap."""
        max_change_pct = CONFIG.get("adaptation", {}).get("max_param_change_pct", 0.20)
        applied = []

        for change in suggestions:
            param = change.get("param", "")
            new_value = change.get("new_value")
            reason = change.get("reason", "")

            if not param or new_value is None:
                continue

            new_value = float(new_value)
            old_value = current_params.get(param)

            if old_value is None:
                old_value = change.get("old_value")
            if old_value is None:
                continue
            old_value = float(old_value)

            # Safety cap
            if old_value != 0:
                change_pct = abs(new_value - old_value) / abs(old_value)
                if change_pct > max_change_pct:
                    direction = 1 if new_value > old_value else -1
                    new_value = old_value + (abs(old_value) * max_change_pct * direction)

            self._db.set_param(param, round(new_value, 4), updated_by="weekly_review")
            applied.append({
                "param": param,
                "old_value": old_value,
                "new_value": round(new_value, 4),
                "reason": reason,
            })

        return applied
