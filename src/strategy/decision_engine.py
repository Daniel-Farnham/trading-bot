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


def _mem_cfg(key: str, default):
    return CONFIG.get("memory", {}).get(key, default)


class DecisionEngine:
    """Orchestrates Claude's weekly investment review."""

    def __init__(
        self,
        thesis_manager: ThesisManager,
        model: str = "sonnet",
        use_extended_thinking: bool = False,
    ):
        self._tm = thesis_manager
        self._model = model
        self._use_extended_thinking = use_extended_thinking

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
        review_type: str = "monthly",
        trade_count: int = 0,
        options_context: str = "",
    ) -> dict:
        """Run a thesis-driven review via Claude.

        Returns parsed decision dict with keys:
            world_assessment, thesis_updates, new_positions,
            close_positions, reduce_positions, lessons, weekly_summary,
            lesson_updates, belief_updates, lessons_to_prune,
            world_view_update, decision_reasoning
        """
        memory_context = self._tm.get_decision_context()
        prompt = self._build_prompt(
            sim_date, memory_context, world_state, technicals_summary,
            fundamentals_summary, portfolio_value, cash, bot_return_pct,
            spy_return_pct, review_number, review_type, trade_count,
            options_context,
        )

        response = self._call_claude(prompt)
        if not response:
            logger.warning("No response from Claude for review on %s", sim_date)
            return self._empty_response()

        # Update memory files based on Claude's decisions
        self._apply_to_memory(response, sim_date)

        return response

    def run_catastrophic_stop_review(
        self,
        sim_date: str,
        ticker: str,
        position_data: dict,
        thesis_data: dict,
        technicals_summary: str,
        world_state: str,
        portfolio_value: float,
        cash: float,
    ) -> dict:
        """Emergency review when a catastrophic stop is hit.

        Claude decides: EXIT (thesis broken), HOLD (temporary panic),
        or ADD (thesis intact + buying opportunity).

        Returns dict with keys: decision ("EXIT" | "HOLD" | "ADD"),
        reasoning, add_allocation_pct (if ADD).
        """
        entry_price = position_data.get("entry_price", 0)
        current_price = position_data.get("current_price", 0)
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        direction = position_data.get("direction", "LONG")

        thesis_text = thesis_data.get("thesis", "(no thesis on file)")
        invalidation = thesis_data.get("invalidation", "(no invalidation criteria)")

        world_view = self._tm.get_world_view()

        prompt = f"""CRITICAL: You are making a decision on {sim_date}.
You DO NOT know what happens after this date.

EMERGENCY REVIEW: CATASTROPHIC STOP HIT

{ticker} has hit -30% from entry. This requires your immediate assessment.

POSITION DATA:
- Ticker: {ticker}
- Direction: {direction}
- Entry Price: ${entry_price:.2f}
- Current Price: ${current_price:.2f}
- P&L: {pnl_pct:+.1f}%
- Portfolio Value: ${portfolio_value:,.2f}
- Cash: ${cash:,.2f}

ORIGINAL THESIS:
{thesis_text}

INVALIDATION CRITERIA:
{invalidation}

CURRENT WORLD VIEW:
{world_view if world_view else "(No world view on file)"}

CURRENT TECHNICALS:
{technicals_summary}

RECENT NEWS:
{world_state}

YOUR TASK:
This position has dropped 30%. You must decide:

1. **EXIT** — The thesis is broken. The drop reflects a fundamental change
   (earnings disaster, competitive disruption, policy reversal, sector rotation).
   Sell immediately and accept the loss.

2. **HOLD** — The thesis is intact. The drop is a market-wide panic, temporary
   sentiment shock, or noise. The original investment case still holds.
   Reset the catastrophic stop to current price -30%.

3. **ADD** — The thesis is STRONGER than when you entered. This is a gift —
   the market is giving you a better price on a thesis you believe in even more.
   Specify additional allocation %. This is the Druckenmiller/Burry move.

Be honest. Most -30% drops are thesis-breaking. Only choose HOLD/ADD if you have
specific evidence the thesis is intact — not just hope.

Respond with ONLY valid JSON:
{{
  "decision": "EXIT" or "HOLD" or "ADD",
  "reasoning": "2-3 sentences explaining why",
  "add_allocation_pct": 0
}}

If decision is ADD, set add_allocation_pct to the ADDITIONAL % to deploy (e.g. 5 for 5% more).
If EXIT or HOLD, set add_allocation_pct to 0."""

        response = self._call_claude(prompt)
        if not response:
            logger.warning("No Claude response for catastrophic stop review of %s — defaulting to EXIT", ticker)
            return {"decision": "EXIT", "reasoning": "Claude failed to respond — safety exit", "add_allocation_pct": 0}

        # Validate response
        decision = response.get("decision", "EXIT").upper()
        if decision not in ("EXIT", "HOLD", "ADD"):
            decision = "EXIT"
        return {
            "decision": decision,
            "reasoning": response.get("reasoning", ""),
            "add_allocation_pct": response.get("add_allocation_pct", 0),
        }

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
        options_context: str = "",
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

        # Shock review urgency note
        shock_section = self._shock_review_text(review_type)

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

        max_new = _mem_cfg("max_new_positions_per_review", 3)

        return f"""CRITICAL: You are making decisions on {sim_date}.
You DO NOT know what happens after this date.
Base your decisions ONLY on the news and data provided below.
Do not reference any events after {sim_date}.

You are the Chief Investment Officer of a thesis-driven trading bot.
Your role is to decide WHAT to own based on how the world is changing.
{shock_section}

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

THIS MONTH'S RESEARCH:
{world_state}

TECHNICAL TIMING DATA:
{technicals_summary if technicals_summary else "(No technical data available)"}

FUNDAMENTALS (quarterly financial data — use to validate thesis quality):
{fundamentals_summary if fundamentals_summary else "(No fundamental data available)"}
NOTE: Unprofitable small/mid-cap companies are CAPPED at "high" confidence.
Large-cap companies ($100B+) bypass this gate.
KEY: RevGr(YoY) is the most important growth metric. Companies growing revenue 30%+ YoY
are structural winners. Companies growing <10% YoY are compounders, not alpha generators.
Your LARGEST positions should be in the FASTEST growing companies, not just the safest ones.
A 25% allocation to a 8% YoY grower will NOT hit 30% annual returns.

{theme_section}

STOCK UNIVERSE (pre-screened candidates you can trade):
{universe_text}
You can also trade stocks outside this universe if you discover them through research.

GOALS:
1. Target 30%+ annualized return — we are concentrated, conviction-driven investors
2. Beat the S&P 500 by 10%+ annually. This requires CONCENTRATED bets, not diversification.
3. Do NOT prioritise capital preservation over growth. We accept drawdowns as the price
   of outsized returns. A 20% drawdown on a 50%+ winner is fine. Sitting in defensive
   stocks that grow 8% YoY is NOT fine — that guarantees underperformance.
4. Even in a falling market, your PRIMARY positions should be high-growth companies
   bought at a discount. Market pullbacks are BUYING opportunities for the best growers,
   not a signal to hide in defensives. Defensive positions should be max 20% of portfolio.
We are Druckenmiller-style macro investors. We identify regime changes, bet big on our
best ideas, and hold winners for months. We use pullbacks as entry opportunities.
We make FEWER trades with LARGER conviction. Quality over quantity.

POSITION TIERS:
You have TWO types of positions:

SCOUT POSITIONS (low/medium confidence, max 5% / 8%):
  - Testing a thesis cheaply. Small, capped bet.
  - These have MECHANICAL stop losses and targets — the system auto-exits at your stated stop/target price daily.
  - Use these when: interesting setup but uncertain timing, or exploring a new theme.

CORE POSITIONS (high/highest confidence, YOU decide the size):
  - Your BEST ideas. The alpha engine. Druckenmiller's "going for the jugular."
  - NO allocation cap — you decide how much to put on. 10%, 20%, 40% — size to conviction.
  - Keep a small cash buffer (5%) for flexibility. Deploy the rest as you see fit.
  - These have a 30% catastrophic safety net (emergency review triggered, not auto-sell).
  - YOU are 100% responsible for exits via thesis reviews.
  - If the thesis is intact, HOLD — even through a 20% drawdown. That's the Burry trade.
  - If the thesis breaks, EXIT IMMEDIATELY — don't hope it comes back.
  - Use "high" when thesis + technicals align strongly.
  - Use "highest" when you want MAXIMUM sizing. Requirements:
    * Thesis is crystal clear with an identifiable catalyst
    * Technicals confirm: above SMA50, MACD bullish, OBV rising (all three)
    * Fundamentals support: profitable company, reasonable valuation
    * Macro regime aligns with the trade direction
  - You CAN open core positions from the very first review if conviction warrants it.
    You do NOT need to go through a scout phase first. If the thesis, technicals,
    fundamentals, and macro all align, go straight to core. Druckenmiller didn't
    "test" his best ideas with 5% — he went big immediately.
  - PYRAMIDING (adding to winners): To add to an existing position, re-submit the ticker
    in new_positions with the TOTAL allocation you want (not the additional amount).
    Example: if you hold NVDA at ~10% and want to go to 25%, submit allocation_pct: 25.
    ONLY pyramid into positions where thesis is STRENGTHENING and OBV is rising.
    Never pyramid into a losing position.
  - When a core position is working, ADD TO IT rather than opening new positions.
    Your biggest winners should be your biggest positions.

RULES:
- Max 8 positions at any time — prefer 3-5 concentrated bets
- Max {max_new} NEW positions per review — be selective, not reactive
- Keep at least 5% cash as a buffer for options and rebalancing
- Every position MUST have a thesis with explicit invalidation conditions
- When a thesis is invalidated, EXIT immediately regardless of tier
- At each review, evaluate EVERY position: is the thesis still valid?
  A position that has lost money but whose thesis is INTACT should be HELD.
  A position whose thesis is BROKEN should be closed regardless of P&L.
- A thesis is BROKEN when the REASON you bought the stock is no longer true:
  * Company loses competitive moat (real competitor emerges, key product obsoleted)
  * Regulatory/legal action against THIS specific company (antitrust, fraud, FDA rejection)
  * Key customer or partner loss (contract cancelled, partnership ended)
  * Fundamental business deterioration (revenue declining, margins collapsing in earnings)
  * Management crisis (CEO departure, accounting scandal)
  * The structural trend you bet on has ended (capex cycle stops, policy reversed)
- A thesis is NOT broken by: price drops, analyst downgrades, sector rotation,
  tariff fears, short-term earnings miss with intact growth story, or general market panic.
  These are noise. Hold through them.
- Use technicals for entry timing. Key exit signals for CORE positions:
  - Thesis invalidated by news, earnings, or policy change
  - Below SMA50 + MACD bearish + OBV falling (triple distribution) — thesis likely broken
  - HOWEVER: a price dip with OBV rising is NOT a sell signal — institutions are accumulating
- For scouts: set tight stops (5-10% below entry). You're testing, not committing.

CRITICAL — MACRO CRASH vs THESIS BREAK (read this carefully):
When 50%+ of your positions show triple distribution SIMULTANEOUSLY, this is a MACRO EVENT
(tariff shock, market panic, rate scare) — NOT individual thesis breaks. During macro events:
1. Do NOT sell positions whose fundamental thesis is still intact. The technicals are reflecting
   market-wide panic, not company-specific deterioration. They WILL recover.
2. The ONLY reason to sell during a macro crash is COMPANY-SPECIFIC bad news (fraud, earnings
   disaster, regulatory action against THAT company specifically).
3. Use RELATIVE STRENGTH as your guide: positions falling LESS than SPY are your strongest
   holdings — these are what institutions will buy back first. Hold or add to these.
4. Do NOT rotate into "defensive" stocks during a macro crash. Defensives underperform in the
   recovery and you'll be stuck in slow-growth names while your original positions bounce 30-40%.
5. Market crashes are BUYING opportunities for your highest-conviction thesis-intact positions.
   Druckenmiller's biggest wins came from buying during panics, not hiding from them.
Triple distribution is a SELL signal only when it's isolated to 1-2 positions while the rest
of the portfolio is fine. When everything is distributing, the signal is noise — hold your nerve.

{discipline_section}

DEPLOYMENT PACING:
{self._deployment_pacing_text(review_number, holdings_count)}

SHORTING:
Consider shorts when trailing SPY or in a declining market.
- Scout shorts: small (3-5%), mechanical stop
- Core shorts: larger (8-12%), thesis-based exits
In a bear market, aim for 1-2 core short positions as hedges.

OPTIONS TRADING:
Options amplify conviction. Druckenmiller's approach: when you have a winning thesis,
go for the jugular — CALLS give you 3-5x leverage with defined max loss.
Don't buy calls to bet on a recovery. Buy calls to AMPLIFY what's already working.
- BUY_CALL: YOUR PRIMARY OPTIONS PLAY. Leveraged upside on winning theses. LEAPS only (3mo+).
  Premium is your max loss — no catastrophic stop needed. 3-5x notional leverage.
  ENTRY GUIDANCE: Prefer entries when OBV rising AND MACD bullish — both green is ideal.
  But use your judgment: if the thesis and catalyst are strong enough, a single bearish
  technical doesn't disqualify a call. What matters is: is the thesis working and do you
  have a clear catalyst with a timeline? Avoid calls during full distribution (OBV falling
  + MACD bearish + below SMA50) — that's a broken setup, not a dip.
  EXPIRY RULE: Pick your expiry based on WHEN the thesis pays off, not a default.
  State the catalyst: "Blackwell earnings hit Q1 2025 → 9 month call" or "NATO defense
  budget vote in June → 8 month call." The expiry must outlast the catalyst by 2+ months.
  EXIT RULE: Close a call when the THESIS is invalidated, not on short-term technical
  noise. A 2-week MACD dip doesn't break a 12-month AI spending thesis. But if the
  reason you bought the call is no longer true (catalyst cancelled, earnings miss,
  competitive moat lost), close immediately regardless of P&L. Also close when time
  value is near zero — at that point you're holding expensive stock, not an option.
  Strike selection: "ATM" (default), "5_OTM", "10_OTM", "5_ITM", "10_ITM".
- SELL_PUT: Get paid to wait for a pullback entry. Cash-secured only.
  If assigned, you own shares at (strike - premium). If not, keep the premium.
  Use when: you want to buy a stock but only at a lower price.
- BUY_PUT: RARELY NEEDED. Only for genuine crisis uncertainty, NOT as default insurance on
  every large position. Concentrated positions are the POINT of this strategy — don't
  hedge away your conviction. A 25% position in a winning thesis does not need a put.

OPTIONS RULES:
- Max 25% of portfolio value in total options premium
- LEAPS only — minimum 3 months to expiry (expiry_months: 3, 6, 9, 12, 18)
- Cash-secured puts must have full assignment cash available
- No naked calls (undefined risk)
- Every option must have a thesis AND a catalyst with a timeline
- You can CLOSE options early using close_options with the contract_id — take profits,
  cut losses, or close when thesis breaks. Don't let options expire worthless
  when they still have time value — close them and redeploy the capital.
{options_context}

WATCHING THESES:
If you see a "Watching" section, these are stopped-out positions with potentially valid theses.
You can re-enter by including in new_positions. Auto-expire after 6 reviews.

TASKS:
1. UPDATE YOUR WORLD VIEW — write a concise macro regime assessment (current regime +
   forward outlook 12-18 months + key risks). This persists between reviews and is your
   primary source of macro continuity. Max 300 words.
2. Review each active thesis — still valid? stronger? weakening?
3. Should we open any new positions? (max {max_new} new per review)
4. Should we SHORT any companies facing structural headwinds?
5. Should we close or reduce any positions? (thesis broken?)
6. Theme check: any themes strengthening or weakening? New themes emerging?
7. Any new lessons learned? Be specific and actionable (include trigger conditions).
{lesson_update_task}
8. For EVERY trade decision (buy, sell, short, reduce, pyramid), write a 1-2 sentence
   reasoning in decision_reasoning. This is your decision journal — it helps you remember
   WHY you made each decision when you review it next month.
9. OPTIONS CHECK: Review the OPTIONS STATUS section. Calls amplify winners — only buy when
   OBV rising AND MACD bullish (both green) and you can name a specific catalyst + timeline.
   For open calls: is the thesis/catalyst still valid? If yes, hold regardless of short-term
   technicals. If thesis is broken or time value is near zero, close and redeploy.
{monthly_section}

Respond with ONLY valid JSON:
{json_schema}

Theme update rules:
- To adjust an existing theme: {{"name": "...", "delta": +1 or -1, "reason": "..."}}
- To add a new theme: {{"name": "...", "action": "ADD", "description": "...", "reason": "..."}}
- Only adjust themes when there's clear evidence from the news. Max ±1 per review.

If no changes needed, return empty arrays. Always include world_assessment, world_view_update,
decision_reasoning, and weekly_summary."""

    def _theme_section_text(self, review_number: int) -> str:
        """Generate the theme section. First review discovers themes from news."""
        if review_number == 1:
            return (
                "THEME DISCOVERY:\n"
                "Based on the news and technical data above, identify 3-4 investment themes you want to pursue.\n"
                "These should reflect the current market environment, not predetermined ideas.\n"
                "Add them via theme_updates with action \"ADD\"."
            )
        num_themes = len(self._tm.get_all_themes())
        at_cap = num_themes >= self._tm._max_themes
        cap_warning = ""
        if at_cap:
            cap_warning = (
                f"\nWARNING: You are at the theme cap ({num_themes}/{self._tm._max_themes}). "
                f"To add a new theme, you MUST first decrement a weaker theme to remove it. "
                f"Look for themes that are no longer supported by recent evidence, have no "
                f"positions tied to them, or scored 1-2 with no recent reinforcement. "
                f"Stale themes waste capacity — be ruthless about pruning."
            )
        return (
            f"THEMES (see Memory section above — scored 1-5, higher = stronger conviction):\n"
            f"Themes are informational — they guide your thinking but don't dictate allocations.\n"
            f"You can propose new themes or adjust scores. New themes start at score 1 and must prove themselves.\n"
            f"If a theme is decremented below 1 it is auto-removed. Max {self._tm._max_themes} themes."
            f"{cap_warning}"
        )

    @staticmethod
    def _shock_review_text(review_type: str) -> str:
        """Generate note for volatility-triggered reviews."""
        if review_type == "low_volatility":
            return """
*** LOW VOLATILITY REVIEW — OPTIONS PREMIUMS ARE CHEAP ***
Market volatility has dropped — options premiums are unusually cheap right now.
This is a LEVERAGE OPPORTUNITY. Druckenmiller goes for the jugular when conviction is high.
1. BUY_CALL LEAPS on your winning theses — 3-5x leverage at low premium cost.
   Which positions have OBV rising + MACD bullish? Those are your CALL candidates.
2. SELL_PUT on names you want to own at a discount — collect rich relative premium.
Low volatility windows don't last. If your thesis is working, amplify it NOW with calls
before the next vol spike makes premiums expensive again.
"""
        if review_type not in ("shock", "volatility"):
            return ""
        return """
*** VOLATILITY REVIEW — SIGNIFICANT MARKET MOVEMENT DETECTED ***
Something has moved unusually. This is NOT a scheduled review.
Step back and reassess calmly — do not panic:
1. Is this a market-wide regime shift or company-specific?
2. For EACH position: has the thesis strengthened, weakened, or broken?
3. If macro-driven and thesis intact, dips with OBV rising are BUYING opportunities.
4. If company-specific and thesis broken, EXIT — don't hope for recovery.
5. Has the macro regime changed? Update your world view if so.
This is a reassessment, not a reaction. Make deliberate decisions.
"""

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
            f"You have executed {trade_count} trades so far. Target: ~15-20 trades per year.\n"
            f"Each trade has real costs (stop-loss risk, slippage, opportunity cost).\n"
            f"Prefer HOLDING and PYRAMIDING existing positions over opening new ones.\n"
            f"The best trade is often no trade — let your winners run."
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
    },
    {
      "ticker": "NVDA",
      "action": "BUY_CALL",
      "allocation_pct": 5,
      "direction": "LONG",
      "thesis": "Blackwell GPU ramp hits Q1 2025 earnings — call captures the catalyst with 2mo buffer",
      "invalidation": "Hyperscalers cut AI capex guidance or Blackwell delayed",
      "strike_selection": "ATM",
      "expiry_months": 9,
      "horizon": "9 months",
      "confidence": "high",
      "timing_note": "OBV rising + MACD bullish — both green. Catalyst: Blackwell earnings Q1 2025"
    },
    {
      "ticker": "AMZN",
      "action": "SELL_PUT",
      "allocation_pct": 8,
      "direction": "LONG",
      "thesis": "Want to own AMZN at 10% discount — sell put to get paid while waiting",
      "invalidation": "AWS growth decelerates below 15%",
      "strike_selection": "10_OTM",
      "expiry_months": 3,
      "horizon": "3 months",
      "confidence": "high",
      "timing_note": "IV elevated after earnings — good premium"
    }
  ],
  "close_positions": [
    {"ticker": "TSLA", "reason": "EV margin thesis broken by competition", "reentry_price": 0}
  ],
  "close_options": [
    {"contract_id": "NVDA_250620C140", "reason": "Taking profits — call up 80%, thesis fully priced in"}
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
  "world_view_update": "Your updated macro regime assessment (current regime + 12-18 month forward outlook + key risks). Max 300 words. This persists between reviews.",
  "decision_reasoning": [
    {"ticker": "NVDA", "action": "BUY", "allocation_pct": 20, "reasoning": "AI capex confirmed by MSFT earnings, OBV rising, RSI pullback to 42 — entering core at size"},
    {"ticker": "NKE", "action": "SELL", "reasoning": "Tariff thesis broken — 25% of supply chain exposed to China tariffs, OBV falling"}
  ],
  "weekly_summary": "Brief narrative for the quarterly summary"
}"""
        return base

    @staticmethod
    def _deployment_pacing_text(review_number: int, holdings_count: int) -> str:
        """Generate deployment pacing guidance based on how many reviews have occurred."""
        if review_number <= 1:
            return (
                "This is your FIRST review. If you have high conviction from the data,\n"
                "go straight to core positions. You do NOT need to scout first.\n"
                "If uncertain, open 1-2 scouts to test the regime. Deploy based on conviction, not protocol."
            )
        elif review_number == 2:
            return (
                "SECOND review. You should be deploying capital with conviction.\n"
                "If existing positions are confirming, PYRAMID into them rather than opening new ones."
            )
        else:
            cash_warning = ""
            if holdings_count <= 2:
                cash_warning = (
                    "\nWARNING: You only have {0} positions. Cash above 40% is underperformance "
                    "unless you're in a confirmed bear market. If you see opportunities, DEPLOY. "
                    "Druckenmiller's edge came from sizing big, not from holding cash."
                ).format(holdings_count)
            return (
                "Deploy capital based on conviction. Your best idea deserves 20-40% of the portfolio.\n"
                "Prefer PYRAMIDING into winning positions over opening new ones.\n"
                "Sitting in cash during a bull market is the biggest risk — you miss the move."
                + cash_warning
            )

    def _call_claude(self, prompt: str) -> dict | None:
        try:
            cmd = [
                "claude", "-p", prompt,
                "--output-format", "text",
                "--model", self._model,
            ]
            if self._use_extended_thinking:
                cmd.extend(["--thinking", "enabled"])
            timeout = 900 if self._model == "opus" else 600
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode != 0:
                logger.error(
                    "Claude review failed (exit %d):\n  STDERR: %s\n  STDOUT (last 500): %s",
                    result.returncode,
                    result.stderr.strip()[:500] or "(empty)",
                    result.stdout.strip()[-500:] or "(empty)",
                )
                return None

            raw = result.stdout.strip()
            if not raw:
                logger.error("Claude returned empty response (exit 0 but no output)")
                return None

            logger.debug("Claude raw response (%d chars): %s", len(raw), raw[:500])

            # Strip markdown code fences
            text = raw
            if "```json" in text:
                text = text.split("```json", 1)[1]
                text = text.split("```", 1)[0]
            elif "```" in text:
                text = text.split("```", 1)[1]
                text = text.split("```", 1)[0]
            text = text.strip()

            if not text:
                logger.error(
                    "Claude response contained no JSON after stripping fences.\n  Raw (first 1000): %s",
                    raw[:1000],
                )
                return None

            data = json.loads(text)

            assessment = data.get("world_assessment", "")
            if assessment:
                logger.info("  World Assessment: %s", assessment[:200])

            return data

        except subprocess.TimeoutExpired:
            logger.error("Claude review timed out after %ds", timeout)
            return None
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse Claude JSON: %s\n  Text attempted (first 1000): %s",
                e, text[:1000] if 'text' in dir() else raw[:1000],
            )
            return None
        except FileNotFoundError:
            logger.error("Claude CLI not found. Is Claude Code installed?")
            return None
        except Exception as e:
            logger.error("Unexpected error calling Claude: %s: %s", type(e).__name__, e)
            return None

    def _apply_to_memory(self, response: dict, sim_date: str) -> None:
        """Write Claude's decisions back to memory files."""
        # Update world view
        world_view = response.get("world_view_update", "")
        if world_view and world_view.strip():
            self._tm.update_world_view(world_view)
            logger.info("  World view updated")

        # Update decision journal
        decision_reasoning = response.get("decision_reasoning", [])
        if decision_reasoning:
            self._tm.append_journal_entry(sim_date, decision_reasoning)
            logger.info("  Decision journal updated (%d entries)", len(decision_reasoning))

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

        # NOTE: close_positions are NOT handled here — they are handled in
        # _execute_decisions AFTER the broker confirms the close succeeded.
        # This prevents orphaned positions (thesis removed but broker close failed).

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
            "close_options": [],
            "reduce_positions": [],
            "theme_updates": [],
            "lessons": [],
            "lesson_updates": [],
            "belief_updates": [],
            "lessons_to_prune": [],
            "world_view_update": "",
            "decision_reasoning": [],
            "weekly_summary": "",
        }
