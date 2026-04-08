"""Live trading orchestrator — the main loop.

Equivalent of thesis_sim.run() but event-driven:
- Call 1: daily at 9:00 AM (discovery + screening)
- Trigger check: every 30 min 9:30 AM - 3:00 PM (no Claude)
- Call 3: Friday 3:30 PM + immediately on trigger (full decision)
- EOD portfolio email: daily at market close (no Claude)
"""
from __future__ import annotations

import logging
import traceback
from datetime import date, datetime
from pathlib import Path

from src.analysis.technical import TechnicalAnalyzer
from src.data.market import MarketData
from src.live.claude_client import ClaudeClient, BudgetExceededError
from src.live.daily_state import DailyState
from src.live.health import update_status
from src.live.executor import LiveExecutor
from src.live.notifier import EmailNotifier
from src.live.pending_orders import PendingOrderTracker
from src.live.prompts import build_call1_prompt, build_call3_prompt
from src.live.reconciler import ReconcileManager
from src.live.research_tools import RESEARCH_TOOLS, ResearchToolExecutor
from src.live.trigger_check import TriggerCheck
from src.live.universe import LiveUniverse
from src.live.watchlist import LiveWatchlist
from src.research.fundamentals import FundamentalsClient
from src.research.news_client import AlpacaNewsClient
from src.research.world_state import build_world_state
from src.strategy.decision_engine import DecisionEngine
from src.strategy.thesis_manager import ThesisManager

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = "data/live/daily_state.json"


class LiveOrchestrator:
    def __init__(
        self,
        claude_client: ClaudeClient,
        decision_engine: DecisionEngine,
        thesis_manager: ThesisManager,
        market_data: MarketData,
        technical_analyzer: TechnicalAnalyzer,
        fundamentals_client: FundamentalsClient,
        news_client: AlpacaNewsClient,
        trigger_check: TriggerCheck,
        executor: LiveExecutor,
        watchlist: LiveWatchlist,
        universe: LiveUniverse,
        notifier: EmailNotifier,
        pending_tracker: PendingOrderTracker | None = None,
        state_path: str = DEFAULT_STATE_PATH,
    ):
        self._claude = claude_client
        self._engine = decision_engine
        self._tm = thesis_manager
        self._market = market_data
        self._technicals = technical_analyzer
        self._fundamentals = fundamentals_client
        self._news = news_client
        self._trigger = trigger_check
        self._executor = executor
        self._watchlist = watchlist
        self._universe = universe
        self._notifier = notifier
        self._state_path = state_path
        self._state = DailyState.load(state_path)
        self._review_count = 0

        # Order tracking and reconciliation
        self._pending = pending_tracker or PendingOrderTracker()
        self._reconciler = ReconcileManager(
            broker=executor._broker,
            market_data=market_data,
            thesis_manager=thesis_manager,
            pending_tracker=self._pending,
        )

        # Reset state if it's a new day
        if not self._state.is_current_day():
            self._state.reset_for_day()
            self._state.save(state_path)

    def run_call1(self) -> None:
        """Daily discovery + screening call (9:00 AM)."""
        logger.info("=== CALL 1: Discovery & Screening ===")

        try:
            # Prune stale watchlist entries
            pruned = self._watchlist.prune()
            if pruned:
                logger.info("Pruned watchlist: %s", pruned)

            # Fetch real Alpaca positions (source of truth)
            alpaca_positions = []
            alpaca_account = {}
            try:
                alpaca_account = self._market.get_account()
                alpaca_positions = self._market.get_positions()
            except Exception as e:
                logger.warning("Failed to fetch Alpaca positions for Call 1: %s", e)

            holdings = self._tm.get_holdings()
            holdings_tickers = [h["ticker"] for h in holdings]
            # Also include tickers from Alpaca in case memory is stale
            alpaca_tickers = [p["ticker"] for p in alpaca_positions]
            holdings_tickers = list(set(holdings_tickers + alpaca_tickers))

            themes_md = self._format_themes()
            world_view_md = self._tm.get_world_view()

            # Pre-fetch news via API (guaranteed baseline)
            prefetched_news = ""
            holdings_news = ""
            try:
                from datetime import timedelta
                yesterday = (date.today() - timedelta(days=1)).isoformat()
                today_str = date.today().isoformat()

                # Broad market news
                broad_articles = self._news.get_news(
                    start_date=yesterday, end_date=today_str, limit=30,
                )
                if broad_articles:
                    prefetched_news = "\n".join(
                        f"- [{a.get('publishedDate', '')[:10]}] {a.get('title', '')} "
                        f"(tickers: {', '.join(a.get('tickers', [])[:5])})"
                        for a in broad_articles
                    )

                # Holdings-specific news
                if holdings_tickers:
                    holdings_articles = self._news.get_news(
                        symbols=holdings_tickers,
                        start_date=yesterday, end_date=today_str, limit=20,
                    )
                    if holdings_articles:
                        holdings_news = "\n".join(
                            f"- [{a.get('publishedDate', '')[:10]}] [{', '.join(a.get('tickers', [])[:3])}] "
                            f"{a.get('title', '')}"
                            for a in holdings_articles
                        )
            except Exception as e:
                logger.warning("Pre-fetch news failed (Claude can still use MCP tools): %s", e)

            tactical_view_md = self._tm.get_tactical_view()

            # Format Alpaca portfolio context for Call 1
            alpaca_context = ""
            if alpaca_account or alpaca_positions:
                parts = []
                if alpaca_account:
                    parts.append(
                        f"Portfolio Value: ${alpaca_account.get('portfolio_value', 0):,.2f} | "
                        f"Cash: ${alpaca_account.get('cash', 0):,.2f}"
                    )
                if alpaca_positions:
                    parts.append("Current Positions (from Alpaca):")
                    for p in alpaca_positions:
                        pnl_pct = p.get("unrealized_pnl_pct", 0) * 100
                        parts.append(
                            f"  - {p['ticker']}: {p['qty']} shares @ ${p['avg_entry']:.2f} "
                            f"(now ${p['current_price']:.2f}, {pnl_pct:+.1f}%)"
                        )
                else:
                    parts.append("No open positions.")

                # Include pending orders
                pending = self._pending.get_all()
                if pending:
                    parts.append("Pending Orders (submitted, awaiting fill):")
                    for o in pending:
                        parts.append(f"  - {o.action} {o.qty} {o.ticker} (retry #{o.retry_count})")

                alpaca_context = "\n".join(parts)

            prompt = build_call1_prompt(
                themes_md=themes_md,
                holdings_tickers=holdings_tickers,
                watchlist_tickers=self._watchlist.get_tickers(),
                universe_tickers=self._universe.get_tickers(),
                world_view_md=world_view_md,
                tactical_view_md=tactical_view_md,
                prefetched_news=prefetched_news,
                holdings_news=holdings_news,
                universe_at_cap=self._universe.is_at_cap(),
                alpaca_portfolio=alpaca_context,
            )

            # Call Claude with research tools for deeper exploration
            tool_executor = ResearchToolExecutor(
                news_client=self._news,
                market_data=self._market,
                technical_analyzer=self._technicals,
                fundamentals_client=self._fundamentals,
            )
            result = self._claude.call(
                prompt, model="sonnet",
                tools=RESEARCH_TOOLS,
                tool_executor=tool_executor,
            )
            update_status("last_call1", datetime.now().isoformat())

            if not result:
                logger.error("Call 1 returned no result")
                return

            # Log Claude's full output for debugging
            import json as _json
            logger.info("Call 1 raw output:\n%s", _json.dumps(result, indent=2)[:3000])

            # Process outputs
            self._state.call1_output = result
            self._state.save(self._state_path)

            # Add flagged tickers to watchlist
            for flagged in result.get("flagged_tickers_universe", []):
                ticker = flagged.get("ticker", "")
                reason = flagged.get("reason", "")
                if ticker:
                    self._watchlist.add(ticker, source="call1", reason=reason)

            # Remove universe tickers Claude flagged for removal (lowest potential)
            holdings_tickers_set = set(holdings_tickers)
            for removal in result.get("universe_removals", []):
                ticker = removal.get("ticker", "")
                reason = removal.get("reason", "")
                if ticker and ticker not in holdings_tickers_set:
                    self._universe.remove(ticker)
                    self._watchlist.remove(ticker)
                    logger.info("Universe: removed %s — %s", ticker, reason[:80])

            # Add new universe additions
            for addition in result.get("new_universe_additions", []):
                ticker = addition.get("ticker", "")
                reason = addition.get("reason", "")
                if ticker:
                    self._universe.add(ticker, source="call1", reason=reason)
                    self._watchlist.add(ticker, source="call1_discovery", reason=reason)

            # Append daily observation to tactical view (not structural)
            observation = result.get("tactical_observation", "")
            if not observation:
                observation = result.get("world_view_observation", "")  # backwards compat
            if observation:
                today = date.today().isoformat()
                current_tv = self._tm.get_tactical_view()
                self._tm.update_tactical_view(f"{current_tv}\n- {today}: {observation}")

            # Send email
            self._notifier.send_call1_summary(result)

            logger.info("Call 1 complete. Flagged %d tickers, %d new universe additions.",
                        len(result.get("flagged_tickers_universe", [])),
                        len(result.get("new_universe_additions", [])))

        except BudgetExceededError as e:
            logger.error("Call 1 blocked by budget: %s", e)
            self._notifier.send_error("BudgetExceeded", str(e))
        except Exception as e:
            logger.error("Call 1 failed: %s", e)
            self._notifier.send_error("Call1Failed", traceback.format_exc())

    def run_trigger_check(self) -> None:
        """Volatility/shock check (every 30 min). No Claude call."""
        try:
            # Step 0: Reconcile pending orders and sync ledger with Alpaca
            try:
                recon_summary = self._reconciler.reconcile()
                if recon_summary.get("orders_retried") or recon_summary.get("orders_filled"):
                    logger.info("Reconciliation actions taken: %s", recon_summary)
            except Exception as e:
                logger.error("Reconciliation failed: %s", e)

            holdings = self._tm.get_holdings()
            holdings_tickers = [h["ticker"] for h in holdings]

            account = self._market.get_account()
            portfolio_value = account.get("portfolio_value", 0)

            result = self._trigger.check(
                holdings_tickers=holdings_tickers,
                watchlist_tickers=self._watchlist.get_tickers(),
                portfolio_value=portfolio_value,
            )
            update_status("last_trigger_check", datetime.now().isoformat())

            if result is None:
                logger.debug("Trigger check: no trigger")
                return

            # Trigger fired — log and fire Call 3 immediately
            logger.info("!!! TRIGGER FIRED: %s — %s", result.trigger_type, result.details)
            self._state.add_trigger(
                result.trigger_type, result.details, result.triggered_tickers,
            )
            self._state.save(self._state_path)

            self._notifier.send_alert(
                f"Trigger: {result.trigger_type}",
                result.details,
            )

            # Fire Call 3 immediately
            self.run_call3(
                review_type=result.trigger_type,
                trigger_reason=result.details,
            )

        except Exception as e:
            logger.error("Trigger check failed: %s", e)

    def run_call3(
        self,
        review_type: str = "weekly",
        trigger_reason: str | None = None,
    ) -> None:
        """Full decision & execution call. Self-sufficient."""
        label = f"Call 3 ({review_type})"
        if trigger_reason:
            label += f" — {trigger_reason}"
        logger.info("=== %s ===", label)

        try:
            # Fetch all data (self-sufficient, like the sim)
            account = self._market.get_account()
            portfolio_value = account.get("portfolio_value", 0)
            cash = account.get("cash", 0)
            positions = self._market.get_positions()

            # Fetch pending orders (e.g. OPG orders queued over weekend)
            pending_orders = []
            try:
                pending_orders = self._executor._broker.get_all_orders(status="open")
            except Exception as e:
                logger.warning("Failed to fetch pending orders: %s", e)

            # Memory context
            memory_context = self._tm.get_decision_context()

            # Append pending orders to memory context so Claude knows what's already queued
            if pending_orders:
                pending_text = "\n\nPENDING ORDERS (already submitted, awaiting fill — DO NOT duplicate):\n"
                for o in pending_orders:
                    pending_text += f"- {o.get('side', '').upper()} {o.get('qty', '')} {o.get('symbol', '')} ({o.get('type', '')})\n"
                memory_context += pending_text

            # Technicals for full universe (like the sim)
            technicals_summary = self._build_technicals_summary()

            # Fundamentals for holdings + universe
            fundamentals_summary = self._build_fundamentals_summary()

            # World state (Call 1 output if available)
            call1_output = self._state.call1_output

            # Performance vs SPY
            bot_return_pct = self._compute_bot_return(portfolio_value)
            spy_return_pct = self._compute_spy_return()

            self._review_count += 1

            # Build prompt via the proven sim prompt builder
            prompt = build_call3_prompt(
                decision_engine=self._engine,
                sim_date=date.today().isoformat(),
                memory_context=memory_context,
                world_state="(Live trading — news provided via Call 1 discovery)",
                technicals_summary=technicals_summary,
                fundamentals_summary=fundamentals_summary,
                portfolio_value=portfolio_value,
                cash=cash,
                bot_return_pct=bot_return_pct,
                spy_return_pct=spy_return_pct,
                review_number=self._review_count,
                review_type=review_type,
                trade_count=self._engine._get_trade_count() if hasattr(self._engine, '_get_trade_count') else 0,
                options_context="",  # Phase 9
                call1_output=call1_output,
            )

            # Call Claude
            result = self._claude.call(prompt, model="sonnet")
            update_status("last_call3", datetime.now().isoformat())

            if not result:
                logger.error("Call 3 returned no result")
                return

            # Log Claude's full output for debugging
            import json as _json
            logger.info("Call 3 raw output:\n%s", _json.dumps(result, indent=2)[:5000])

            self._state.call3_output = result
            self._state.save(self._state_path)

            # Execute trades FIRST — before writing memory
            trades = self._executor.execute_decisions(
                response=result,
                portfolio_value=portfolio_value,
                cash=cash,
                positions=positions,
            )

            for trade in trades:
                self._state.add_trade(trade)
            self._state.save(self._state_path)

            # Track new position orders in pending_orders.json
            # These will be confirmed via reconciliation, not assumed filled
            new_position_actions = {"BUY (CORE)", "BUY (SCOUT)", "SHORT", "PYRAMID",
                                    "BUY_CALL", "BUY_PUT", "SELL_PUT"}
            pending_tickers = set()
            for trade in trades:
                if trade.get("action") in new_position_actions and trade.get("order_id"):
                    self._pending.add(
                        order_id=trade["order_id"],
                        ticker=trade["ticker"],
                        action=trade["action"],
                        qty=trade.get("quantity", 0),
                        confidence=trade.get("confidence", ""),
                        thesis_snippet=trade.get("thesis_snippet", ""),
                    )
                    pending_tickers.add(trade["ticker"])

            # Filter result for memory writes:
            # - CLOSE/REDUCE: write immediately (confirmed by Alpaca)
            # - New positions: defer until fill confirmed via reconciliation
            executed_closes = {t["ticker"] for t in trades if t.get("action") == "CLOSE"}
            executed_reduces = {t["ticker"] for t in trades if t.get("action") == "REDUCE"}
            confirmed_tickers = executed_closes | executed_reduces

            filtered_result = dict(result)

            # Exclude new positions from memory — they're pending, not confirmed
            filtered_result["new_positions"] = []

            # Only keep close_positions that were actually executed
            filtered_result["close_positions"] = [
                c for c in result.get("close_positions", [])
                if c.get("ticker", "") in executed_closes
            ]

            # Keep decision_reasoning for confirmed trades + non-trade actions (HOLD, thesis reviews)
            trade_actions = {"BUY", "SELL", "SHORT", "REDUCE", "BUY_CALL", "BUY_PUT", "SELL_PUT"}
            filtered_result["decision_reasoning"] = [
                r for r in result.get("decision_reasoning", [])
                if r.get("action", "").upper() not in trade_actions  # Keep HOLD, review, etc.
                or r.get("ticker", "") in confirmed_tickers
            ]

            # Apply memory updates with filtered result
            # (themes, lessons, world view are written regardless — they're observations, not trades)
            # New positions will be written to memory when fills are confirmed by reconciler
            self._engine._apply_to_memory(filtered_result, date.today().isoformat())

            # Update trigger check reference point
            self._trigger.set_last_call3_value(portfolio_value)

            # Send email
            self._notifier.send_call3_summary(
                result, trades,
                review_type=review_type,
                trigger_reason=trigger_reason,
            )

            logger.info("Call 3 complete. %d trades executed.", len(trades))

        except BudgetExceededError as e:
            logger.error("Call 3 blocked by budget: %s", e)
            self._notifier.send_error("BudgetExceeded", str(e))
        except Exception as e:
            logger.error("Call 3 failed: %s", e)
            self._notifier.send_error("Call3Failed", traceback.format_exc())

    def run_eod_portfolio(self) -> None:
        """EOD portfolio email — no Claude call, just raw data + md files."""
        logger.info("=== EOD Portfolio Update ===")
        try:
            account = self._market.get_account()
            positions = self._market.get_positions()

            # Convert positions to the format the notifier expects
            formatted_positions = []
            for p in positions:
                formatted_positions.append({
                    "symbol": p.get("ticker", ""),
                    "qty": p.get("qty", 0),
                    "avg_entry_price": p.get("avg_entry", 0),
                    "current_price": p.get("current_price", 0),
                    "market_value": p.get("market_value", 0),
                    "unrealized_pl": p.get("unrealized_pnl", 0),
                    "unrealized_plpc": p.get("unrealized_pnl_pct", 0),
                })

            # Sync ledger with closing prices so memory is fresh for next day
            try:
                self._reconciler.reconcile()
                logger.info("EOD ledger synced with Alpaca closing prices.")
            except Exception as e:
                logger.error("EOD ledger sync failed: %s", e)

            memory_dir = str(self._tm._paths.get("theses", Path("data/live")).parent)
            self._notifier.send_eod_portfolio(account, formatted_positions, memory_dir)

            logger.info("EOD portfolio email sent.")
        except Exception as e:
            logger.error("EOD portfolio failed: %s", e)

    def reconcile_on_startup(self) -> None:
        """Sync Alpaca positions with thesis memory on startup."""
        logger.info("=== Reconcile on startup ===")
        try:
            # Run full reconciliation (pending orders + ledger sync)
            summary = self._reconciler.reconcile()
            logger.info("Startup reconciliation: %s", summary)

            # Set trigger check reference
            account = self._market.get_account()
            self._trigger.set_last_call3_value(account.get("portfolio_value", 0))

        except Exception as e:
            logger.error("Reconcile failed: %s", e)

    def initialize_first_boot(self) -> None:
        """Cold start: quarterly-synthesis of 12 months of news → world view + 3 themes + watchlist."""
        logger.info("=== First boot initialization ===")

        # Seed universe from config
        added = self._universe.seed_from_config()
        logger.info("Seeded %d tickers into universe", added)

        try:
            from datetime import timedelta

            # Build 4 quarters: current quarter + previous 3
            today = date.today()
            quarters = self._build_quarter_ranges(today)
            universe_tickers = self._universe.get_tickers()

            # PHASE 1: Summarize each quarter individually
            logger.info("Phase 1: Synthesizing 4 quarterly summaries...")
            quarterly_summaries = []
            for q_label, q_start, q_end, is_current in quarters:
                logger.info("  Fetching news for %s (%s → %s)...", q_label, q_start, q_end)
                try:
                    q_news = build_world_state(
                        start_date=q_start,
                        end_date=q_end,
                        holdings=None,
                        watchlist=universe_tickers[:30],
                        client=self._news,
                    )
                except Exception as e:
                    logger.warning("  Failed to fetch news for %s: %s", q_label, e)
                    continue

                if not q_news:
                    logger.warning("  No news for %s", q_label)
                    continue

                # Fetch SPY performance for this quarter
                spy_return_pct, spy_regime = self._get_spy_quarter_performance(q_start, q_end)
                spy_context = ""
                if spy_return_pct is not None:
                    spy_context = f"\nSPY PERFORMANCE THIS QUARTER: {spy_return_pct:+.1f}% ({spy_regime})\n"
                    logger.info("  %s SPY: %+.1f%% (%s)", q_label, spy_return_pct, spy_regime)

                current_label = " (partial, current)" if is_current else ""
                q_prompt = f"""You are analyzing financial news from {q_label}{current_label}.
{spy_context}
NEWS HEADLINES FROM {q_label}:
{q_news}

Your task: Summarize what happened this quarter AND give a forward view looking out 12-18 months from THIS quarter's perspective.

The SPY performance tells you the market regime this quarter — use it to contextualize the headlines.
A bull quarter with defensive headlines is different from a bear quarter with defensive headlines.

Be concise. Focus on structural shifts and persistent themes, not one-off events.

Respond with ONLY valid JSON:
{{
  "quarter": "{q_label}",
  "spy_return_pct": {spy_return_pct if spy_return_pct is not None else "null"},
  "market_regime": "{spy_regime or 'unknown'}",
  "what_happened": "2-3 sentences: key events, regime shifts, sector leadership this quarter",
  "persistent_patterns": "1-2 sentences: what patterns were building that will likely continue",
  "forward_view": "1-2 sentences: looking forward 12-18 months from {q_label}, where is the world heading? what positioning makes sense?"
}}"""

                q_result = self._claude.call(q_prompt, model="sonnet")
                if q_result:
                    # Ensure SPY data is in the result even if Claude doesn't include it
                    if spy_return_pct is not None and "spy_return_pct" not in q_result:
                        q_result["spy_return_pct"] = spy_return_pct
                    if spy_regime and "market_regime" not in q_result:
                        q_result["market_regime"] = spy_regime
                    quarterly_summaries.append(q_result)
                    logger.info("  %s summary: %s", q_label, q_result.get("what_happened", "")[:100])

            if not quarterly_summaries:
                logger.error("No quarterly summaries generated — first boot aborted")
                return

            # PHASE 2: Synthesize 4 quarterly summaries → world view + 3 themes + watchlist
            logger.info("Phase 2: Synthesizing final world view from %d quarterly summaries...",
                        len(quarterly_summaries))

            def _fmt_summary(s):
                quarter = s.get("quarter", "Unknown")
                spy_ret = s.get("spy_return_pct")
                regime = s.get("market_regime", "unknown")
                spy_line = f"**SPY:** {spy_ret:+.1f}% ({regime})\n" if spy_ret is not None else ""
                return (
                    f"### {quarter}\n"
                    f"{spy_line}"
                    f"**What happened:** {s.get('what_happened', '')}\n"
                    f"**Persistent patterns:** {s.get('persistent_patterns', '')}\n"
                    f"**Forward view from this quarter:** {s.get('forward_view', '')}"
                )

            summaries_text = "\n\n".join(_fmt_summary(s) for s in quarterly_summaries)

            # Compute overall SPY trajectory across the 4 quarters
            spy_trajectory = self._describe_spy_trajectory(quarterly_summaries)

            synth_prompt = f"""You are initializing a Druckenmiller-style macro trading bot.

Below are quarterly summaries covering the last 12 months. Each quarter has equal weight —
do not over-index on the most recent quarter just because it's freshest.

MARKET TRAJECTORY (SPY performance by quarter):
{spy_trajectory}

QUARTERLY SUMMARIES:
{summaries_text}

STOCK UNIVERSE ({len(universe_tickers)} stocks):
{', '.join(universe_tickers)}

YOUR TASKS:

1. Write an initial WORLD VIEW with three sections:
   a. RECENT HISTORY (2-4 sentences): What happened across all 4 quarters that got us here?
      Synthesize the progression — how did the world evolve quarter by quarter?
   b. CURRENT MACRO REGIME (1 paragraph): What is the state of the world right now?
   c. FORWARD OUTLOOK 12-18 MONTHS (1 paragraph): Where is the world going?
      Weigh the forward views from each quarter — which predictions have held up,
      which have shifted? What does the trajectory suggest?
   Max 600 words total.

2. Identify EXACTLY 3 initial THEMES — your strongest, highest-conviction ideas.
   These should be themes with evidence across MULTIPLE quarters, not just the most recent.
   Score 1-5 based on how strong and persistent the evidence is.

3. Pick an initial WATCHLIST — up to 20 stocks from the universe (or outside it) that
   represent your best ideas for the current environment.

4. Note any key observations.

Respond with ONLY valid JSON:
{{
  "world_view": "Your world view with all three sections. Max 600 words.",
  "themes": [
    {{"name": "Theme Name", "score": 4, "description": "Why this theme matters — include which quarters showed evidence"}}
  ],
  "initial_watchlist": [
    {{"ticker": "NVDA", "reason": "Why this stock fits the themes"}}
  ],
  "observations": "Key patterns you noticed"
}}"""

            result = self._claude.call(synth_prompt, model="sonnet")
            if not result:
                logger.warning("Final synthesis Claude call returned no result")
                return

            # Write world view
            world_view = result.get("world_view", "")
            if world_view:
                self._tm.update_world_view(world_view)
                logger.info("Initial world view written (%d chars)", len(world_view))

            # Write themes (capped at 3)
            themes = result.get("themes", [])[:3]
            for theme in themes:
                name = theme.get("name", "")
                score = theme.get("score", 1)
                desc = theme.get("description", "")
                if name:
                    self._tm.add_theme(name, desc, score)
                    logger.info("Initial theme: %s [%d] — %s", name, score, desc[:80])

            # Populate initial watchlist
            watchlist_picks = result.get("initial_watchlist", [])
            for pick in watchlist_picks:
                ticker = pick.get("ticker", "")
                reason = pick.get("reason", "")
                if ticker:
                    self._watchlist.add(ticker, source="first_boot", reason=reason)
                    self._universe.add(ticker, source="first_boot", reason=reason)
            logger.info("Initial watchlist: %d stocks", len(watchlist_picks))

            observations = result.get("observations", "")
            if observations:
                logger.info("First boot observations: %s", observations[:200])

            self._notifier.send_alert(
                "First Boot Complete",
                f"Quarterly synthesis complete. {len(quarterly_summaries)} quarters analyzed. "
                f"{len(themes)} themes, {len(watchlist_picks)} stocks watchlisted.",
            )

        except BudgetExceededError as e:
            logger.error("First boot blocked by budget: %s", e)
        except Exception as e:
            logger.error("First boot headline synthesis failed: %s", e)
            logger.info("Call 1 will start building context from scratch")

    @staticmethod
    def _build_quarter_ranges(today: date) -> list[tuple[str, str, str, bool]]:
        """Build list of (label, start_date, end_date, is_current) for last 4 quarters."""
        from datetime import timedelta

        # Determine current quarter
        current_year = today.year
        current_q = (today.month - 1) // 3 + 1

        quarters = []
        for offset in range(3, -1, -1):  # 3 quarters back → current
            q_num = current_q - offset
            q_year = current_year
            while q_num <= 0:
                q_num += 4
                q_year -= 1

            # Quarter start/end
            q_start_month = (q_num - 1) * 3 + 1
            q_start = date(q_year, q_start_month, 1)

            if q_num == 4:
                q_end = date(q_year, 12, 31)
            else:
                next_start = date(q_year, q_start_month + 3, 1)
                q_end = next_start - timedelta(days=1)

            # Cap current quarter at today
            is_current = (offset == 0)
            if is_current and q_end > today:
                q_end = today

            label = f"Q{q_num} {q_year}"
            quarters.append((label, q_start.isoformat(), q_end.isoformat(), is_current))

        return quarters

    def _get_spy_quarter_performance(self, q_start: str, q_end: str) -> tuple[float | None, str | None]:
        """Fetch SPY return % for a given quarter from Alpaca bars.

        Returns (return_pct, regime_label) or (None, None) on failure.
        """
        try:
            from datetime import datetime as _dt
            start_dt = _dt.fromisoformat(q_start)
            end_dt = _dt.fromisoformat(q_end)

            bars = self._market.get_bars("SPY", start=start_dt, end=end_dt, limit=100)
            if bars.empty or len(bars) < 2:
                return None, None

            start_price = float(bars.iloc[0]["close"])
            end_price = float(bars.iloc[-1]["close"])
            if start_price <= 0:
                return None, None

            pct = ((end_price - start_price) / start_price) * 100

            if pct >= 8:
                regime = "strong bull"
            elif pct >= 3:
                regime = "bull"
            elif pct >= -3:
                regime = "flat"
            elif pct >= -8:
                regime = "bear"
            else:
                regime = "strong bear"

            return round(pct, 1), regime
        except Exception as e:
            logger.debug("SPY quarter performance failed: %s", e)
            return None, None

    @staticmethod
    def _describe_spy_trajectory(quarterly_summaries: list[dict]) -> str:
        """Describe the overall SPY trajectory across quarters."""
        lines = []
        returns = []
        for s in quarterly_summaries:
            q = s.get("quarter", "?")
            r = s.get("spy_return_pct")
            regime = s.get("market_regime", "unknown")
            if r is not None:
                lines.append(f"- {q}: {r:+.1f}% ({regime})")
                returns.append(r)
            else:
                lines.append(f"- {q}: data unavailable")

        if len(returns) >= 2:
            total = sum(returns)
            lines.append(f"\nCumulative 4Q: {total:+.1f}%")

            # Describe overall trajectory
            if len(returns) >= 3:
                first_half = sum(returns[:2]) / 2
                second_half = sum(returns[-2:]) / 2
                if second_half - first_half > 5:
                    lines.append("Trajectory: accelerating rally (bear→bull recovery)")
                elif first_half - second_half > 5:
                    lines.append("Trajectory: decelerating (bull→bear transition)")
                elif all(r > 3 for r in returns):
                    lines.append("Trajectory: sustained bull market")
                elif all(r < -3 for r in returns):
                    lines.append("Trajectory: sustained bear market")
                else:
                    lines.append("Trajectory: choppy/sideways")

        return "\n".join(lines)

    def _format_themes(self) -> str:
        """Format themes for prompt context."""
        themes = self._tm.get_all_themes()
        if not themes:
            return "(No themes yet)"
        lines = []
        for t in themes:
            name = t.get("name", "")
            score = t.get("score", 1)
            desc = t.get("description", "")
            lines.append(f"- {name} [{score}]: {desc}")
        return "\n".join(lines)

    def _compute_bot_return(self, current_value: float) -> float:
        """Compute bot return % from Alpaca account history."""
        try:
            account = self._market.get_account()
            # Use Alpaca's last_equity as starting reference
            # For a more accurate measure, track initial deposit separately
            last_equity = float(account.get("last_equity", current_value))
            if last_equity > 0:
                return ((current_value - last_equity) / last_equity) * 100
        except Exception:
            pass
        return 0.0

    def _compute_spy_return(self) -> float:
        """Compute SPY return % over the same period."""
        try:
            from datetime import timedelta
            from datetime import timedelta as _td
            bars = self._market.get_bars("SPY", start=datetime.now() - _td(days=60), limit=30)
            if not bars.empty and len(bars) >= 2:
                start_price = float(bars.iloc[0]["close"])
                end_price = float(bars.iloc[-1]["close"])
                if start_price > 0:
                    return ((end_price - start_price) / start_price) * 100
        except Exception:
            pass
        return 0.0

    def _build_technicals_summary(self) -> str:
        """Build technicals for holdings + universe from live Alpaca bars."""
        holdings = self._tm.get_holdings()
        holdings_tickers = [h["ticker"] for h in holdings]
        universe_tickers = self._universe.get_tickers()
        all_tickers = list(set(holdings_tickers + universe_tickers))

        from datetime import timedelta
        bar_start = datetime.now() - timedelta(days=120)

        lines = []
        for ticker in all_tickers:
            try:
                bars = self._market.get_bars(ticker, start=bar_start, limit=60)
                if bars.empty or len(bars) < 20:
                    continue
                snap = self._technicals.analyze(ticker, bars)
                line = self._format_snapshot(snap, ticker in holdings_tickers)
                if line:
                    lines.append(line)
            except Exception as e:
                logger.debug("Technicals failed for %s: %s", ticker, e)
                continue

        return "\n".join(lines) if lines else "(No technical data available)"

    def _build_fundamentals_summary(self) -> str:
        """Build fundamentals for holdings + universe."""
        holdings = self._tm.get_holdings()
        holdings_tickers = [h["ticker"] for h in holdings]
        universe_tickers = self._universe.get_tickers()
        all_tickers = list(set(holdings_tickers + universe_tickers))

        try:
            from src.research.fundamentals import build_fundamentals_prompt_section
            return build_fundamentals_prompt_section(
                self._fundamentals, all_tickers,
            )
        except Exception as e:
            logger.warning("Fundamentals summary failed: %s", e)
            return "(No fundamental data available)"

    @staticmethod
    def _format_snapshot(snap, is_holding: bool = False) -> str:
        """Format a TechnicalSnapshot for the prompt."""
        prefix = "[HELD] " if is_holding else ""
        parts = [f"{prefix}{snap.ticker}: ${snap.close:.2f}"]

        if snap.rsi is not None:
            parts.append(f"RSI={snap.rsi:.0f}")
        if snap.macd_signal is not None:
            signal = "bullish" if snap.macd_signal == "bullish" else "bearish"
            parts.append(f"MACD={signal}")
        if snap.sma50 is not None:
            above = "above" if snap.close > snap.sma50 else "below"
            parts.append(f"SMA50={above}")
        if snap.obv_trend is not None:
            parts.append(f"OBV={snap.obv_trend}")
        if snap.atr_pct is not None:
            parts.append(f"ATR={snap.atr_pct:.1f}%")
        if snap.hv_percentile is not None:
            parts.append(f"HV={snap.hv_percentile:.0f}pctl")

        return " | ".join(parts)
