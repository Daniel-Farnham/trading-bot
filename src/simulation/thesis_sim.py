"""V3 Thesis-Driven Simulation Engine.

Steps through historical dates week by week, orchestrating the full
research -> decision -> execution loop. Memory files persist and evolve
across sim weeks, giving Claude continuity.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from alpaca.data.timeframe import TimeFrame

from src.analysis.technical import TechnicalAnalyzer, TechnicalSnapshot
from src.config import CONFIG
from src.data.market import MarketData
from src.research.fundamentals import (
    FundamentalsClient,
    build_fundamentals_prompt_section,
)
from src.research.news_client import AlpacaNewsClient
from src.research.world_state import build_world_state
from src.simulation.sim_broker import SimBroker
from src.strategy.decision_engine import DecisionEngine
from src.strategy.risk_v3 import RiskManagerV3, V3PositionPlan, V3RiskVeto
from src.strategy.thesis_manager import ThesisManager

logger = logging.getLogger(__name__)


class ThesisSimulation:
    """Weekly-cadence simulation engine for V3 thesis-driven strategy."""

    def __init__(
        self,
        start_date: str,
        end_date: str,
        initial_cash: float = 100_000.0,
        review_cadence_days: int = 21,
        monthly_review_cadence_days: int = 63,
        data_dir: str | Path | None = None,
        seed_themes: list[tuple[str, str]] | None = None,
        seed_beliefs_path: str | Path | None = None,
        volatility_cooldown_days: int = 7,
        disable_news: bool = False,
        model: str = "sonnet",
        use_extended_thinking: bool = False,
    ):
        self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = datetime.strptime(end_date, "%Y-%m-%d")
        self.initial_cash = initial_cash
        self.review_cadence = review_cadence_days
        self.monthly_cadence = monthly_review_cadence_days
        self._volatility_cooldown = volatility_cooldown_days
        self._disable_news = disable_news
        self._seed_themes = seed_themes or []
        self._seed_beliefs_path = Path(seed_beliefs_path) if seed_beliefs_path else None

        # Data directory for memory files
        self._data_dir = Path(data_dir) if data_dir else Path("data/v3_sim")
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Components
        self.market = MarketData()
        self.news_client = AlpacaNewsClient()
        self.technicals = TechnicalAnalyzer()
        self.risk = RiskManagerV3()
        self.broker = SimBroker(initial_cash=initial_cash)

        # Memory system — isolated to sim data dir
        self.thesis_manager = ThesisManager(base_dir=self._data_dir)
        # Override paths: all in-sim files go to data_dir
        self.thesis_manager._paths = {
            "theses": self._data_dir / "active_theses.md",
            "ledger": self._data_dir / "portfolio_ledger.md",
            "summaries": self._data_dir / "quarterly_summaries.md",
            "lessons": self._data_dir / "lessons_learned.md",
            "themes": self._data_dir / "themes.md",
            "beliefs": self._data_dir / "beliefs.md",
            "world_view": self._data_dir / "world_view.md",
            "journal": self._data_dir / "decision_journal.md",
        }

        self.decision_engine = DecisionEngine(
            thesis_manager=self.thesis_manager,
            model=model,
            use_extended_thinking=use_extended_thinking,
        )

        # Fundamentals client
        self.fundamentals = FundamentalsClient(
            cache_dir=self._data_dir / "fundamentals_cache",
        )

        # Tracking
        self.daily_snapshots: list[dict] = []
        self._peak_value = initial_cash
        self._all_bars: dict[str, pd.DataFrame] = {}
        self._trading_days: list = []
        self._review_decisions: list[dict] = []
        self._weeks_elapsed = 0
        self._max_new_per_review = CONFIG.get("memory", {}).get("max_new_positions_per_review", 3)
        self._spy_snapshot = None  # Cached SPY technicals for relative strength
        self._atr_cache: dict[str, float] = {}  # ticker -> ATR% for shock detection
        self._hv_cache: dict[str, float] = {}  # ticker -> HV percentile for low-vol trigger
        self._hv_prev: dict[str, float] = {}  # previous HV percentiles (debounce)
        self._spy_hv_pctl: float = 50.0  # SPY HV percentile for market-wide vol trigger
        self._spy_hv_prev: float = 50.0  # previous SPY HV (debounce)
        self._last_snapshots: dict = {}  # ticker -> TechnicalSnapshot from latest review

    def run(self) -> dict:
        """Run the full thesis-driven simulation."""
        logger.info(
            "V3 Thesis Simulation: %s to %s | Cash: $%s",
            self.start_date.strftime("%Y-%m-%d"),
            self.end_date.strftime("%Y-%m-%d"),
            f"{self.initial_cash:,.0f}",
        )

        # Clear previous sim memory
        self.thesis_manager.clear_all()

        # Seed beliefs if provided (pre-load cross-regime principles)
        if self._seed_beliefs_path and self._seed_beliefs_path.exists():
            import shutil
            dest = self.thesis_manager._paths["beliefs"]
            shutil.copy2(self._seed_beliefs_path, dest)
            logger.info("Seeded beliefs from %s", self._seed_beliefs_path)

        # Seed macro themes if provided (start at score 1, must prove themselves)
        if self._seed_themes:
            for name, desc in self._seed_themes:
                self.thesis_manager.add_theme(name, desc, score=1)
            logger.info("Seeded %d macro themes (score 1)", len(self._seed_themes))

        # Download historical data for the full curated universe + SPY benchmark
        universe = self._get_universe_tickers()
        logger.info("Downloading historical data for %d tickers + SPY benchmark...", len(universe))
        all_tickers = universe if "SPY" in universe else universe + ["SPY"]
        self._all_bars = self._download_bars(all_tickers)
        self._trading_days = self._get_trading_days()
        logger.info("Got %d trading days across %d tickers.", len(self._trading_days), len(self._all_bars))

        # Pre-fetch fundamentals for the universe
        logger.info("Prefetching fundamentals for %d tickers...", len(universe))
        self.fundamentals.prefetch_universe(universe)

        # Pre-research phase: gather 3 months of news before sim starts
        # to give Claude context on prevailing trends and emerging themes
        # (always runs — even with news disabled, the world view matters)
        self._run_pre_research(universe)

        # Main simulation loop — step day by day, review every N days
        days_since_review = self.review_cadence  # Trigger review on first trading day
        days_since_monthly = 0

        for i, day in enumerate(self._trading_days):
            day_dt = datetime.combine(day, datetime.min.time())

            # Daily: update prices, check catastrophic stops
            daily_bars = self._get_daily_bars(day)

            # Snapshot previous prices BEFORE update (for shock detection)
            prev_prices = {
                ticker: pos.current_price
                for ticker, pos in self.broker.positions.items()
                if pos.current_price
            }

            self.broker.update_prices(daily_bars)
            self.broker.reprice_options(daily_bars, str(day))
            expired_options = self.broker.check_option_expiry(str(day), daily_bars)
            for exp in expired_options:
                logger.info("  OPTION %s: %s", exp.get("exit_reason", "expired"), exp.get("contract_id", ""))
            self._check_catastrophic_stops(daily_bars, day_dt)
            self._update_ledger_values(daily_bars, day)
            self._record_snapshot(day)

            # Update ATR + HV cache for held positions (used by volatility triggers)
            for ticker in self.broker.positions:
                bars_df = self._all_bars.get(ticker)
                if bars_df is not None:
                    history = self._get_bars_up_to(bars_df, day)
                    if not history.empty and len(history) >= 20:
                        snap = self.technicals.analyze(ticker, history)
                        if snap.atr_pct is not None:
                            self._atr_cache[ticker] = snap.atr_pct
                        if snap.hv_percentile is not None:
                            self._hv_cache[ticker] = snap.hv_percentile

            # Update SPY HV for market-wide low-vol trigger
            spy_bars_df = self._all_bars.get("SPY")
            if spy_bars_df is not None:
                spy_history = self._get_bars_up_to(spy_bars_df, day)
                if not spy_history.empty and len(spy_history) >= 20:
                    spy_snap = self.technicals.analyze("SPY", spy_history)
                    if spy_snap.hv_percentile is not None:
                        self._spy_hv_pctl = spy_snap.hv_percentile

            # Track peak for drawdown
            pv = self.broker.portfolio_value
            if pv > self._peak_value:
                self._peak_value = pv

            days_since_review += 1
            days_since_monthly += 1

            # Check for volatility trigger (consolidates shock + drift detection)
            # Cooldown prevents over-trading on inherently volatile stocks
            volatility_triggered = False
            low_vol_triggered = False
            if days_since_review >= self._volatility_cooldown and self.broker.positions:
                shock = self._check_intraday_shock(daily_bars, prev_prices)
                drift = self._check_volatility_trigger(days_since_review)
                volatility_triggered = shock or drift
                # Low-vol trigger: options become cheap when HV drops
                if not volatility_triggered:
                    low_vol_triggered = self._check_low_vol_trigger()

            # Scheduled review OR volatility-triggered reassessment
            if days_since_review >= self.review_cadence or volatility_triggered or low_vol_triggered:
                if not self.risk.check_drawdown(pv, self._peak_value):
                    dd = (self._peak_value - pv) / self._peak_value * 100
                    logger.warning("  Drawdown %.1f%% — skipping review, stops still active", dd)
                else:
                    if low_vol_triggered and days_since_review < self.review_cadence:
                        review_type = "low_volatility"
                        logger.info("")
                        logger.info("  !!! LOW VOLATILITY — options are cheap, triggering options review")
                    elif volatility_triggered and days_since_review < self.review_cadence:
                        review_type = "volatility"
                        logger.info("")
                        logger.info("  !!! VOLATILITY DETECTED — triggering reassessment review")
                    else:
                        review_type = "monthly" if days_since_monthly >= self.monthly_cadence else "weekly"
                    self._run_review(day, day_dt, review_type)
                    if review_type == "monthly":
                        days_since_monthly = 0
                    self._weeks_elapsed += 1
                days_since_review = 0

            # Progress logging
            if (i + 1) % 20 == 0:
                ret = ((pv / self.initial_cash) - 1) * 100
                pos_count = len(self.broker.positions)
                opts_count = len(self.broker.option_positions)
                if opts_count:
                    logger.info(
                        ">>> Day %d/%d (%s) | $%s (%+.1f%%) | %d positions + %d opts",
                        i + 1, len(self._trading_days), day, f"{pv:,.0f}", ret,
                        pos_count, opts_count,
                    )
                else:
                    logger.info(
                        ">>> Day %d/%d (%s) | $%s (%+.1f%%) | %d positions",
                        i + 1, len(self._trading_days), day, f"{pv:,.0f}", ret, pos_count,
                    )

        report = self._build_report()
        return report

    def _run_pre_research(self, universe: list[str]) -> None:
        """Gather 3 months of news before sim starts and ask Claude to synthesize.

        This gives Claude context on prevailing trends, emerging themes, and
        macro regime before it makes its first trade — like a fund manager
        reading 3 months of research reports on day one.
        """
        pre_start = self.start_date - timedelta(days=90)
        pre_end = self.start_date - timedelta(days=1)

        logger.info("")
        logger.info("=" * 60)
        logger.info("  PRE-RESEARCH PHASE: %s to %s", pre_start.strftime("%Y-%m-%d"), pre_end.strftime("%Y-%m-%d"))
        logger.info("  Gathering 3 months of news to establish macro context...")
        logger.info("=" * 60)

        # Build world state from 3 months of news in chunks (API limits)
        all_news = []
        chunk_days = 14
        cursor = pre_start
        while cursor < pre_end:
            chunk_end = min(cursor + timedelta(days=chunk_days), pre_end)
            try:
                chunk = build_world_state(
                    start_date=cursor.strftime("%Y-%m-%d"),
                    end_date=chunk_end.strftime("%Y-%m-%d"),
                    holdings=None,
                    watchlist=universe[:30],  # Top 30 tickers to keep API calls reasonable
                    client=self.news_client,
                )
                if chunk and chunk.strip() and "unavailable" not in chunk.lower():
                    all_news.append(f"--- {cursor.strftime('%b %d')} to {chunk_end.strftime('%b %d')} ---\n{chunk}")
            except Exception as e:
                logger.warning("Pre-research chunk failed (%s to %s): %s", cursor, chunk_end, e)
            cursor = chunk_end + timedelta(days=1)

        if not all_news:
            logger.warning("No pre-research news gathered — starting cold")
            return

        combined_news = "\n\n".join(all_news)
        # Truncate if too long (keep most recent)
        if len(combined_news) > 30000:
            combined_news = combined_news[-30000:]

        # Ask Claude to synthesize into a world view
        prompt = f"""You are a macro investment researcher preparing a briefing for a new fund manager
who is about to start trading on {self.start_date.strftime('%Y-%m-%d')}.

Below is 3 months of market news from {pre_start.strftime('%Y-%m-%d')} to {pre_end.strftime('%Y-%m-%d')}.
Synthesize this into a concise world view briefing.

NEWS:
{combined_news}

Write a briefing with these sections (respond with ONLY valid JSON):
{{
  "world_view": "Current macro regime assessment + 12-18 month forward outlook + key risks. Max 300 words.",
  "themes": [
    {{"name": "Theme Name", "description": "What this theme is about and which stocks benefit", "conviction": "high/medium/low"}}
  ],
  "top_opportunities": [
    {{"ticker": "SYMBOL", "reasoning": "Why this stock is well-positioned based on the last 3 months of trends"}}
  ],
  "key_risks": ["Risk 1", "Risk 2"]
}}

Identify 3-5 investment themes and 5-8 top opportunities. Focus on structural trends,
not short-term noise. Which sectors are seeing institutional accumulation? Which policy
changes are creating structural winners/losers? What's the dominant macro narrative?"""

        response = self.decision_engine._call_claude(prompt)
        if not response:
            logger.warning("Pre-research Claude call failed — starting cold")
            return

        # Seed the world view
        world_view = response.get("world_view", "")
        if world_view:
            self.thesis_manager.update_world_view(world_view)
            logger.info("  Pre-research world view seeded")

        # Seed themes from pre-research (score 2 — above default but must prove themselves)
        themes = response.get("themes", [])
        for t in themes:
            name = t.get("name", "")
            desc = t.get("description", "")
            if name and desc:
                conviction = t.get("conviction", "medium")
                score = 3 if conviction == "high" else 2 if conviction == "medium" else 1
                self.thesis_manager.add_theme(name, desc, score=score)
                logger.info("  Theme discovered: %s [%d]", name, score)

        # Log top opportunities for visibility
        opportunities = response.get("top_opportunities", [])
        if opportunities:
            logger.info("  Top opportunities identified:")
            for opp in opportunities[:8]:
                logger.info("    %s: %s", opp.get("ticker", "?"), opp.get("reasoning", "")[:100])

        key_risks = response.get("key_risks", [])
        if key_risks:
            logger.info("  Key risks: %s", "; ".join(key_risks[:5]))

        logger.info("  Pre-research phase complete")
        logger.info("")

    def _due_diligence_check(
        self, ticker: str, direction: str, thesis: str,
        day_dt: datetime, lookback_days: int = 30,
    ) -> bool:
        """Fetch recent headlines for a ticker and ask Claude to confirm the trade.

        Shows ALL headlines (positive and negative) — no filtering bias.
        Claude sees its own thesis alongside recent news and decides whether
        to proceed or cancel.

        Returns True if Claude confirms, False to cancel.
        """
        start = day_dt - timedelta(days=lookback_days)
        try:
            articles = self.news_client.get_ticker_news(
                tickers=[ticker],
                start_date=start.strftime("%Y-%m-%d"),
                end_date=day_dt.strftime("%Y-%m-%d"),
            )
        except Exception:
            return True  # No news = proceed

        if not articles:
            return True  # No news = proceed

        headlines = [a.get("title", "") for a in articles if a.get("title")][:10]
        if not headlines:
            return True

        headlines_text = "\n".join(f"  - {h}" for h in headlines)
        logger.info("    DUE DILIGENCE %s: reviewing %d recent headlines", ticker, len(headlines))

        prompt = f"""CRITICAL: You are making a decision on {day_dt.strftime('%Y-%m-%d')}.
You DO NOT know what happens after this date.

You proposed {direction} {ticker} with this thesis:
"{thesis[:300]}"

Here are the most recent headlines about {ticker}:
{headlines_text}

IMPORTANT: Negative sentiment alone is NOT a reason to cancel. Analyst downgrades,
bearish opinion pieces, and "overvalued" commentary are just opinions — they do not
change a company's fundamentals. Most negative articles are noise.

Only cancel if the headlines reveal STRUCTURAL headwinds that directly break your thesis:
- Government/regulatory action (Congressional investigation, antitrust lawsuit, sanctions)
- Material business deterioration (major customer loss, fraud, accounting issues)
- Structural competitive shift (key product obsoleted, market share collapse)

If the news is just sentiment, opinions, or general market commentary — PROCEED.

Respond with ONLY valid JSON:
{{
  "proceed": true or false,
  "reasoning": "One sentence explaining your decision"
}}"""

        response = self.decision_engine._call_claude(prompt)
        if not response:
            return True  # Claude failed to respond — proceed

        proceed = response.get("proceed", True)
        reasoning = response.get("reasoning", "")
        if proceed:
            logger.info("    DUE DILIGENCE CONFIRMED %s: %s", ticker, reasoning[:100])
        else:
            logger.info("    DUE DILIGENCE REJECTED %s: %s", ticker, reasoning[:100])
        return proceed

    def _execute_option_trade(
        self, new_pos: dict, daily_bars: dict, day_dt: datetime,
    ) -> None:
        """Execute an options trade (BUY_CALL, BUY_PUT, SELL_PUT)."""
        from src.options.pricing import (
            select_strike, quote_option, expiry_date_from_months,
            DEFAULT_RISK_FREE_RATE,
        )

        ticker = new_pos.get("ticker", "")
        action = new_pos.get("action", "").upper()
        allocation_pct = new_pos.get("allocation_pct", 5) / 100.0
        strike_selection = new_pos.get("strike_selection", "ATM")
        expiry_months = new_pos.get("expiry_months", 6)

        # Get underlying price
        bar = daily_bars.get(ticker)
        if not bar:
            price = self._get_or_download_price(ticker, day_dt)
            if not price:
                logger.warning("    No price for %s, skipping option", ticker)
                return
        else:
            price = bar["close"]

        # Determine option type and direction
        if action == "BUY_CALL":
            option_type = "CALL"
            is_short = False
        elif action == "BUY_PUT":
            option_type = "PUT"
            is_short = False
        elif action == "SELL_PUT":
            option_type = "PUT"
            is_short = True
        else:
            logger.warning("    Unknown option action: %s", action)
            return

        # Calculate strike and expiry
        strike = select_strike(price, strike_selection, option_type)
        expiry = expiry_date_from_months(str(day_dt.date()), expiry_months)

        # Get volatility from ATR cache or default
        sigma = 0.30  # Default 30%
        atr_pct = self._atr_cache.get(ticker)
        if atr_pct is not None:
            # Convert daily ATR% to annualised vol: ATR% * sqrt(252) / 100
            import math
            sigma = max(0.15, min(1.0, (atr_pct / 100.0) * math.sqrt(252)))

        # Get quote
        from src.options.pricing import time_to_expiry_years
        T = time_to_expiry_years(str(day_dt.date()), expiry)
        if T <= 0:
            logger.warning("    Option expiry %s is in the past, skipping", expiry)
            return

        quote = quote_option(price, strike, T, DEFAULT_RISK_FREE_RATE, sigma, option_type)
        premium = quote.premium

        if premium <= 0.01:
            logger.warning("    Option premium too low ($%.2f), skipping", premium)
            return

        # Calculate number of contracts
        budget = self.broker.portfolio_value * allocation_pct
        contracts = max(1, int(budget / (premium * 100)))

        # Check options premium limit (max 15% of portfolio)
        greeks = self.broker.get_portfolio_greeks()
        current_options_pct = greeks["total_options_value"] / self.broker.portfolio_value if self.broker.portfolio_value > 0 else 0
        new_premium_cost = premium * 100 * contracts
        new_options_pct = (greeks["total_options_value"] + new_premium_cost) / self.broker.portfolio_value
        if new_options_pct > 0.15:
            logger.info("    OPTIONS CAP: %s would push options to %.1f%% of portfolio (max 15%%)", ticker, new_options_pct * 100)
            return

        # Build contract ID
        expiry_short = expiry.replace("-", "")[2:]  # "250620"
        type_char = "C" if option_type == "CALL" else "P"
        contract_id = f"{ticker}_{expiry_short}{type_char}{strike:.0f}"

        # Place the order
        result = self.broker.place_option_order(
            contract_id=contract_id,
            ticker=ticker,
            option_type=option_type,
            strike=strike,
            expiry=expiry,
            quantity=contracts,
            premium=premium,
            is_short=is_short,
            entry_date=str(day_dt.date()),
            sigma=sigma,
        )

        if result.success:
            total_cost = premium * 100 * contracts
            side = "SOLD" if is_short else "BOUGHT"
            logger.info(
                "    OPTION: %s %d %s %s $%.0f expiry %s @ $%.2f/sh ($%s total, delta %.2f)",
                side, contracts, ticker, option_type, strike, expiry,
                premium, f"{total_cost:,.0f}", quote.greeks.delta,
            )
        else:
            logger.warning("    Option order failed for %s: %s", ticker, result.error)

    def _build_options_context(self) -> str:
        """Build options context string for Claude's prompt.

        Always returns content — when no options are open, shows budget
        and opportunity signals to actively prompt Claude to consider options.
        """
        pv = self.broker.portfolio_value
        greeks = self.broker.get_portfolio_greeks()
        max_premium_pct = 0.15
        current_options_value = greeks["total_options_value"]
        budget = pv * max_premium_pct - current_options_value
        budget = max(0.0, budget)

        lines = ["\nOPTIONS STATUS:"]

        # Show open positions if any
        if greeks["option_count"] > 0:
            lines.append("  Open Option Positions (use contract_id in close_options to exit early):")
            for contract_id, opt in self.broker.option_positions.items():
                pnl = (opt.current_premium - opt.premium_paid) * 100 * opt.quantity
                if opt.is_short:
                    pnl = -pnl
                pnl_pct = ((opt.current_premium - opt.premium_paid) / opt.premium_paid * 100) if opt.premium_paid > 0 else 0
                if opt.is_short:
                    pnl_pct = -pnl_pct
                side = "SHORT" if opt.is_short else "LONG"

                # Days to expiry
                days_left = 0
                if self.daily_snapshots:
                    from src.options.pricing import time_to_expiry_years
                    t = time_to_expiry_years(self.daily_snapshots[-1]["date"], opt.expiry)
                    days_left = max(0, int(t * 365))

                # Intrinsic vs time value
                snap = self._last_snapshots.get(opt.ticker)
                spot = snap.current_price if snap else 0
                intrinsic_str = ""
                if spot > 0:
                    if opt.option_type == "CALL":
                        intrinsic = max(0.0, spot - opt.strike)
                    else:
                        intrinsic = max(0.0, opt.strike - spot)
                    time_val = max(0.0, opt.current_premium - intrinsic)
                    intrinsic_str = f" | Intrinsic: ${intrinsic:.2f}, Time: ${time_val:.2f}"

                lines.append(
                    f"    [{contract_id}] {side} {opt.ticker} {opt.option_type} ${opt.strike:.0f} "
                    f"x{opt.quantity} | ${opt.premium_paid:.2f}→${opt.current_premium:.2f} ({pnl_pct:+.0f}%) | "
                    f"Delta: {opt.current_delta:.2f} | {days_left}d left{intrinsic_str}"
                )
            lines.append(f"  Net Delta: {greeks['net_delta']:.0f} shares equivalent")
            lines.append(f"  Total Options Value: ${current_options_value:,.0f}")
        else:
            lines.append("  Open Options: None")

        lines.append(f"  Options Budget Available: ${budget:,.0f} (15% cap = ${pv * max_premium_pct:,.0f})")

        # Generate opportunity signals from current portfolio + technicals
        opportunities = self._detect_options_opportunities()
        if opportunities:
            lines.append("")
            lines.append("  OPTIONS DATA (volatility context for your decision-making):")
            for opp in opportunities:
                lines.append(f"  → {opp}")

        return "\n".join(lines)

    def _detect_options_opportunities(self) -> list[str]:
        """Scan portfolio and technicals for actionable options opportunities.

        Biased toward BUY_CALL (amplify winners) over BUY_PUT (insurance).
        Druckenmiller uses leverage on conviction, not hedging on fear.
        """
        opportunities = []
        pv = self.broker.portfolio_value
        if pv <= 0:
            return opportunities

        # Check each held position — prioritize CALL opportunities
        for ticker, pos in self.broker.positions.items():
            snap = self._last_snapshots.get(ticker)
            if snap is None:
                continue

            position_value = pos.quantity * pos.current_price
            position_pct = (position_value / pv) * 100
            hv_pctl = snap.hv_percentile
            hv = snap.hv_20

            # Strong thesis + OBV rising → BUY_CALL candidate (primary signal)
            if snap.obv_trend == "rising" and snap.is_macd_bullish:
                hv_note = ""
                if hv_pctl is not None and hv_pctl < 40 and hv is not None:
                    hv_note = f" HV at {hv_pctl:.0f}th pctl — premiums are cheap."
                opportunities.append(
                    f"{ticker} ({position_pct:.0f}% position) — OBV rising + MACD bullish. "
                    f"BUY_CALL LEAPS would give 3-5x leveraged exposure on this winning thesis.{hv_note}"
                )

            # High HV percentile → premium is rich, good for selling
            if hv_pctl is not None and hv_pctl > 75 and hv is not None:
                opportunities.append(
                    f"{ticker} HV={hv:.0f}% at {hv_pctl:.0f}th percentile — premium is RICH. "
                    f"SELL_PUT to collect elevated premium if you want to add on a pullback."
                )

        # Check non-held tickers for sell-put entries
        for ticker, snap in self._last_snapshots.items():
            if ticker in self.broker.positions:
                continue
            if snap.rsi_14 is not None and snap.rsi_14 > 68:
                if snap.hv_percentile is not None and snap.hv_percentile > 60:
                    opportunities.append(
                        f"{ticker} RSI={snap.rsi_14:.0f} (extended) with HV at {snap.hv_percentile:.0f}th pctl: "
                        f"SELL_PUT 10% OTM to get paid while waiting for a better entry."
                    )

        return opportunities

    def _get_trade_count(self) -> int:
        """Get total trade count: closed trades + open positions."""
        return len(self.broker.closed_trades) + len(self.broker.positions)

    def _run_review(self, day, day_dt: datetime, review_type: str) -> None:
        """Execute a weekly/monthly thesis review."""
        label = review_type.upper()
        logger.info("")
        logger.info("=" * 60)
        logger.info("  %s REVIEW | %s", label, day)
        equity_val = self.broker.equity_value
        options_val = self.broker.options_value
        if self.broker.option_positions:
            logger.info(
                "  Portfolio: $%s (Stocks: $%s + Options: $%s) | Cash: $%s | Positions: %d + %d opts",
                f"{self.broker.portfolio_value:,.0f}",
                f"{equity_val:,.0f}", f"{options_val:,.0f}",
                f"{self.broker.cash:,.0f}",
                len(self.broker.positions), len(self.broker.option_positions),
            )
        else:
            logger.info(
                "  Portfolio: $%s | Cash: $%s | Positions: %d",
                f"{self.broker.portfolio_value:,.0f}", f"{self.broker.cash:,.0f}",
                len(self.broker.positions),
            )
        logger.info("=" * 60)

        # Tick watching thesis expiry
        expired = self.thesis_manager.tick_watching()
        if expired:
            logger.info("  Watching theses expired: %s", ", ".join(expired))

        # Step 1: Research — build world state from Alpaca News
        holdings_tickers = [h["ticker"] for h in self.thesis_manager.get_holdings()]
        universe_tickers = self._get_universe_tickers()

        if self._disable_news:
            world_state = "(News feed disabled — decide based on technicals and fundamentals only)"
        else:
            week_start = day_dt - timedelta(days=self.review_cadence)
            try:
                world_state = build_world_state(
                    start_date=week_start.strftime("%Y-%m-%d"),
                    end_date=day_dt.strftime("%Y-%m-%d"),
                    holdings=holdings_tickers or None,
                    watchlist=universe_tickers,
                    client=self.news_client,
                )
            except Exception as e:
                logger.warning("Failed to build world state: %s", e)
                world_state = "(News unavailable this week)"

        # Step 2: Technicals for current holdings + watchlist sample
        technicals_summary = self._build_technicals_summary(day)

        # Step 2b: Fundamentals for watchlist + holdings
        fundamentals_tickers = list(set(
            universe_tickers + holdings_tickers
        ))
        fundamentals_summary = build_fundamentals_prompt_section(
            self.fundamentals, fundamentals_tickers, as_of=day_dt,
        )

        # Step 2c: Build options context
        options_context = self._build_options_context()

        # Step 3: Claude review
        pv = self.broker.portfolio_value
        bot_return = ((pv / self.initial_cash) - 1) * 100
        spy_return = self._get_spy_return(day)
        trade_count = self._get_trade_count()
        logger.info("  Bot: %+.1f%% | SPY: %+.1f%% | Trades: %d | Calling Claude for %s review...",
                     bot_return, spy_return, trade_count, review_type)
        response = self.decision_engine.run_weekly_review(
            sim_date=str(day),
            world_state=world_state,
            technicals_summary=technicals_summary,
            fundamentals_summary=fundamentals_summary,
            portfolio_value=pv,
            cash=self.broker.cash,
            bot_return_pct=bot_return,
            spy_return_pct=spy_return,
            review_number=self._weeks_elapsed + 1,
            review_type=review_type,
            trade_count=trade_count,
            options_context=options_context,
        )

        # Step 4: Execute decisions
        daily_bars = self._get_daily_bars(day)
        self._execute_decisions(response, daily_bars, day_dt)

        # Step 5: Quarterly summary check (every ~13 weeks)
        if self._weeks_elapsed > 0 and self._weeks_elapsed % 13 == 0:
            quarter_num = (self._weeks_elapsed // 13)
            year = day_dt.year
            summary = response.get("weekly_summary", "No summary provided.")
            pv = self.broker.portfolio_value
            ret = ((pv / self.initial_cash) - 1) * 100
            full_summary = (
                f"**Performance:** {ret:+.1f}% (${pv:,.0f})\n"
                f"**Positions:** {len(self.broker.positions)}\n"
                f"**Summary:** {summary}"
            )
            self.thesis_manager.append_summary(f"Q{quarter_num}", year, full_summary)
            logger.info("  Wrote quarterly summary Q%d %d", quarter_num, year)

        self._review_decisions.append({
            "date": str(day),
            "type": review_type,
            "response": response,
        })

        changes = (
            len(response.get("new_positions", []))
            + len(response.get("close_positions", []))
            + len(response.get("close_options", []))
            + len(response.get("reduce_positions", []))
        )
        logger.info("  Review complete: %d action(s)", changes)

        # Consistency check: ensure every open position has an active thesis
        for ticker in list(self.broker.positions.keys()):
            thesis = self.thesis_manager.get_by_ticker(ticker)
            if not thesis:
                logger.warning(
                    "  ORPHAN POSITION: %s in portfolio but has no active thesis — re-adding thesis stub",
                    ticker,
                )
                pos = self.broker.positions[ticker]
                self.thesis_manager.add_thesis(
                    ticker=ticker,
                    direction="SHORT" if pos.is_short else "LONG",
                    thesis="(Thesis orphaned — position exists without thesis record)",
                    entry_price=pos.entry_price,
                    target_price=0.0,
                    stop_price=pos.stop_loss,
                    confidence="medium",
                )

    def _compute_dynamic_stop(self, ticker: str, day) -> float | None:
        """Compute dynamic stop % based on 3x ATR%, floored at 8%, capped at 20%.

        Returns the stop as a decimal (e.g. 0.12 for 12%). None if ATR unavailable.
        """
        bars = self._all_bars.get(ticker)
        if bars is None:
            return None
        history = self._get_bars_up_to(bars, day)
        if history.empty or len(history) < 20:
            return None
        snap = self.technicals.analyze(ticker, history)
        if snap.atr_pct is None:
            return None
        # Catastrophic safety net only — Claude manages normal exits via thesis reviews
        # Fixed 25% stop prevents black swan disasters
        return 0.25

    def _execute_decisions(self, response: dict, daily_bars: dict, day_dt: datetime) -> None:
        """Translate Claude's decisions into simulated trades."""
        positions = self.broker.get_positions_list()
        position_tickers = [p["ticker"] for p in positions]

        # Close positions — move thesis to watching ONLY after broker confirms close
        for close in response.get("close_positions", []):
            ticker = close.get("ticker", "")
            if ticker not in position_tickers:
                continue
            bar = daily_bars.get(ticker)
            price = bar["close"] if bar else None
            if price:
                result = self.broker.close_position(ticker, price)
                if result.success:
                    self.thesis_manager.remove_position(ticker)
                    reason = close.get("reason", "thesis invalidated")
                    reentry_price = close.get("reentry_price", None)
                    if reentry_price == 0:
                        reentry_price = None
                    self.thesis_manager.move_to_watching(
                        ticker, exit_price=price, reason=reason,
                        reentry_price=reentry_price,
                    )
                    logger.info("    SOLD %s @ $%.2f → WATCHING — %s", ticker, price, reason[:80])

        # Close option positions early
        for close_opt in response.get("close_options", []):
            contract_id = close_opt.get("contract_id", "")
            if contract_id not in self.broker.option_positions:
                logger.warning("    Option %s not found — skipping close", contract_id)
                continue
            opt = self.broker.option_positions[contract_id]
            result = self.broker.close_option_position(contract_id, opt.current_premium)
            if result.success:
                pnl = result.order_id  # close_option_position stores pnl info
                reason = close_opt.get("reason", "closed early")
                # Calculate P&L for logging
                if opt.is_short:
                    trade_pnl = (opt.premium_paid - opt.current_premium) * 100 * opt.quantity
                else:
                    trade_pnl = (opt.current_premium - opt.premium_paid) * 100 * opt.quantity
                side = "SHORT" if opt.is_short else "LONG"
                logger.info(
                    "    CLOSED OPTION [%s] %s %s %s $%.0f @ $%.2f (P&L: $%s) — %s",
                    contract_id, side, opt.ticker, opt.option_type, opt.strike,
                    opt.current_premium, f"{trade_pnl:+,.0f}", reason[:80],
                )
                self._sync_options_to_ledger()

        # Reduce positions
        for reduce in response.get("reduce_positions", []):
            ticker = reduce.get("ticker", "")
            if ticker not in position_tickers:
                continue
            pos = self.broker.positions.get(ticker)
            if not pos:
                continue
            bar = daily_bars.get(ticker)
            price = bar["close"] if bar else None
            if not price:
                continue

            shares_to_sell = self.risk.evaluate_reduce(
                ticker, reduce.get("new_allocation_pct", 5),
                pos.quantity, price, self.broker.portfolio_value,
            )
            if shares_to_sell > 0 and shares_to_sell < pos.quantity:
                # Partial close: close full position, re-open smaller
                self.broker.close_position(ticker, price)
                remaining = pos.quantity - shares_to_sell
                from src.strategy.risk_v3 import PositionPlan
                reopen_plan = PositionPlan(
                    ticker=ticker, quantity=remaining,
                    entry_price=price, stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit,
                    risk_amount=0, position_value=remaining * price,
                    risk_pct=0, is_short=pos.is_short,
                )
                self.broker.place_bracket_order(reopen_plan, is_short=pos.is_short, opened_at=day_dt.isoformat())
                logger.info("    REDUCED %s by %d shares @ $%.2f", ticker, shares_to_sell, price)

        # New positions (and tier upgrades) — enforce max new positions per review
        new_position_count = 0
        for new_pos in response.get("new_positions", []):
            ticker = new_pos.get("ticker", "")
            if not ticker:
                continue

            # Route options trades BEFORE the pyramid check — options on a held
            # ticker (e.g., BUY_PUT on NVDA while holding NVDA shares) are not pyramids
            action = new_pos.get("action", "BUY").upper()
            if action in ("BUY_CALL", "BUY_PUT", "SELL_PUT"):
                self._execute_option_trade(new_pos, daily_bars, day_dt)
                self._sync_options_to_ledger()
                continue

            # Check if this is a pyramid/upgrade on an existing position
            if ticker in position_tickers:
                existing_pos = self.broker.positions.get(ticker)
                if not existing_pos:
                    continue

                new_confidence = new_pos.get("confidence", "medium")
                is_now_core = self.risk.is_core_position(new_confidence)

                # Upgrade scout → core: widen stops
                was_scout = not self.risk.is_core_position(
                    # Guess old confidence from stop width — if stop is tight, was scout
                    "medium"  # Default assumption
                )
                if is_now_core:
                    old_stop = existing_pos.stop_loss
                    if existing_pos.is_short:
                        new_stop = existing_pos.entry_price * 100.0
                        new_target = existing_pos.entry_price * 0.01
                    else:
                        new_stop = 0.01
                        new_target = existing_pos.entry_price * 100.0
                    self.broker.update_stops(ticker, new_stop, new_target)
                    logger.info(
                        "    UPGRADED %s → CORE (%s) | mechanical stops REMOVED (thesis-based exits only)",
                        ticker, new_confidence,
                    )

                # Pyramid: add shares if Claude requested a larger allocation
                target_alloc = new_pos.get("allocation_pct", 0) / 100.0
                current_value = existing_pos.quantity * (existing_pos.current_price or existing_pos.entry_price)
                current_alloc = current_value / self.broker.portfolio_value if self.broker.portfolio_value > 0 else 0
                additional_alloc = target_alloc - current_alloc

                if additional_alloc > 0.02:  # Only pyramid if adding >2% allocation
                    bar = daily_bars.get(ticker)
                    price = bar["close"] if bar else existing_pos.current_price
                    if price and price > 0:
                        additional_value = self.broker.portfolio_value * additional_alloc
                        # Respect cash reserve
                        min_cash = self.broker.portfolio_value * self.risk._min_cash_pct
                        available = self.broker.cash - min_cash
                        additional_value = min(additional_value, max(0, available))

                        import math
                        add_qty = math.floor(additional_value / price)
                        if add_qty > 0:
                            result = self.broker.add_to_position(ticker, add_qty, price)
                            if result.success:
                                self.thesis_manager.update_position(
                                    ticker=ticker, side=existing_pos.is_short and "SHORT" or "LONG",
                                    qty=existing_pos.quantity, entry_price=existing_pos.entry_price,
                                    current_value=existing_pos.quantity * price,
                                    date_opened=existing_pos.opened_at[:10],
                                )
                                logger.info(
                                    "    PYRAMIDED %s: +%d shares @ $%.2f (now %d shares, avg $%.2f, ~%.0f%% alloc)",
                                    ticker, add_qty, price, existing_pos.quantity,
                                    existing_pos.entry_price, target_alloc * 100,
                                )
                continue

            # Enforce max new positions per review (pyramids/upgrades don't count)
            if new_position_count >= self._max_new_per_review:
                logger.info("    CAPPED %s: max %d new positions per review reached", ticker, self._max_new_per_review)
                self.thesis_manager.remove_thesis(ticker)
                continue
            new_position_count += 1

            # Due diligence — fetch recent headlines and let Claude review
            # (skipped when news is disabled — Claude decides on technicals + fundamentals)
            if not self._disable_news:
                direction = new_pos.get("direction", "LONG").upper()
                dd_result = self._due_diligence_check(
                    ticker, direction, new_pos.get("thesis", ""), day_dt,
                )
                if not dd_result:
                    logger.info("    DUE DILIGENCE REJECTED %s — Claude reconsidered after reviewing recent news", ticker)
                    self.thesis_manager.remove_thesis(ticker)
                    continue

            bar = daily_bars.get(ticker)
            if not bar:
                # On-demand: download bars for a newly discovered ticker
                price = self._get_or_download_price(ticker, day_dt)
                if not price:
                    logger.warning("    No price data for %s, skipping", ticker)
                    # Clean up thesis that was added with entry_price=0.0
                    self.thesis_manager.remove_thesis(ticker)
                    continue
            else:
                price = bar["close"]

            # Compute dynamic stop based on ATR
            dynamic_stop = self._compute_dynamic_stop(ticker, day_dt.date())
            if dynamic_stop is None:
                dynamic_stop = 0.25  # Fall back to 25% catastrophic safety net
                logger.debug("    No ATR for %s, using default 18%% stop", ticker)
            else:
                logger.debug("    Dynamic stop for %s: %.1f%%", ticker, dynamic_stop * 100)

            # Check profitability for fundamentals gate
            # Large-cap companies ($100B+) bypass the gate — data lag shouldn't
            # prevent sizing into AMZN, GOOGL, META etc.
            ticker_profitable = self.fundamentals.is_profitable(
                ticker, as_of=day_dt,
            )
            if ticker_profitable is False and self.fundamentals.is_large_cap(ticker):
                ticker_profitable = True
                logger.info(
                    "    LARGE-CAP BYPASS: %s is large-cap ($100B+), bypassing profitability gate",
                    ticker,
                )

            plan = self.risk.evaluate_new_position(
                ticker=ticker,
                side=new_pos.get("direction", "LONG"),
                allocation_pct=new_pos.get("allocation_pct", 6),
                price=price,
                portfolio_value=self.broker.portfolio_value,
                cash=self.broker.cash,
                open_position_count=len(self.broker.positions),
                existing_tickers=position_tickers,
                short_exposure=self.broker.get_short_exposure(),
                thesis=new_pos.get("thesis", ""),
                dynamic_stop_pct=dynamic_stop,
                confidence=new_pos.get("confidence", "medium"),
                is_profitable=ticker_profitable,
            )

            if isinstance(plan, V3RiskVeto):
                logger.info("    VETOED %s: %s", ticker, plan.reason)
                # Clean up thesis that was added before execution
                self.thesis_manager.remove_thesis(ticker)
                continue

            # Convert to SimBroker-compatible plan
            from src.strategy.risk_v3 import PositionPlan
            is_short = plan.side == "SHORT"
            confidence = new_pos.get("confidence", "medium")
            is_core = self.risk.is_core_position(confidence)

            if is_core:
                # Core positions: NO mechanical stop or target. Thesis is the only exit.
                # Claude manages all exits via thesis reviews.
                if is_short:
                    stop_loss = plan.entry_price * 100.0   # Unreachable
                    take_profit = plan.entry_price * 0.01  # Unreachable
                else:
                    stop_loss = 0.01                        # Unreachable
                    take_profit = plan.entry_price * 100.0  # Unreachable
                logger.info(
                    "    CORE POSITION %s (%s confidence) — thesis-based exits ONLY, no mechanical stops",
                    ticker, confidence,
                )
            else:
                # Scout positions: mechanical stop + target
                claude_stop = new_pos.get("stop_price")
                if claude_stop and claude_stop > 0:
                    stop_loss = float(claude_stop)
                    logger.info(
                        "    SCOUT stop for %s: $%.2f (vs catastrophic $%.2f)",
                        ticker, stop_loss, plan.catastrophic_stop,
                    )
                else:
                    stop_loss = plan.catastrophic_stop

                if is_short:
                    take_profit = new_pos.get("target_price", plan.entry_price * 0.5)
                else:
                    take_profit = new_pos.get("target_price", plan.entry_price * 2)

            sim_plan = PositionPlan(
                ticker=plan.ticker,
                quantity=plan.quantity,
                entry_price=plan.entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_amount=0,
                position_value=plan.position_value,
                risk_pct=0,
                is_short=is_short,
            )

            result = self.broker.place_bracket_order(
                sim_plan, is_short=is_short, opened_at=day_dt.isoformat(),
            )

            if result.success:
                # Update thesis with actual entry price
                self.thesis_manager.update_thesis(ticker, entry_price=price)
                # Update portfolio ledger
                self.thesis_manager.update_position(
                    ticker=ticker, side=plan.side,
                    qty=plan.quantity, entry_price=price,
                    current_value=plan.position_value,
                    date_opened=day_dt.strftime("%Y-%m-%d"),
                )
                position_tickers.append(ticker)
                action = "SHORTED" if is_short else "BOUGHT"
                tier = "CORE" if is_core else "SCOUT"
                stop_pct = abs(stop_loss - price) / price * 100
                logger.info(
                    "    %s %d %s @ $%.2f (%s%% alloc, %s, stop $%.2f [%.1f%%])",
                    action, plan.quantity, ticker, price,
                    plan.allocation_pct, tier, stop_loss, stop_pct,
                )

    def _sync_options_to_ledger(self) -> None:
        """Sync broker option positions to thesis_manager's cached options.

        Must be called after any option placement or closure so that subsequent
        equity ledger rebuilds don't wipe the options section.
        """
        if self.broker.option_positions:
            options_data = []
            for opt in self.broker.option_positions.values():
                options_data.append({
                    "ticker": opt.ticker,
                    "option_type": opt.option_type,
                    "strike": opt.strike,
                    "expiry": opt.expiry,
                    "quantity": opt.quantity,
                    "premium_paid": opt.premium_paid,
                    "current_premium": opt.current_premium,
                    "is_short": opt.is_short,
                })
            self.thesis_manager._current_options = options_data
        else:
            self.thesis_manager._current_options = None

    def _check_intraday_shock(
        self, daily_bars: dict,
        prev_prices: dict[str, float] | None = None,
        atr_multiple: float = 3.0,
        portfolio_threshold: float = -0.05,
    ) -> bool:
        """Check for intraday shocks that warrant a reassessment review.

        Two triggers:
        1. Any single position drops more than 3x its ATR in a day (adaptive
           to each stock's normal volatility — PLTR at 5% ATR needs a 15% drop,
           COST at 1.4% ATR triggers at 4.2%)
        2. Total portfolio drops 5%+ in a day (market-wide crash)

        Uses prev_prices (yesterday's close) for close-to-close comparison
        that catches gap-down opens.
        """
        if not self.broker.positions:
            return False

        prev_prices = prev_prices or {}

        # Check individual positions using ATR-scaled thresholds
        for ticker, pos in self.broker.positions.items():
            bar = daily_bars.get(ticker)
            if not bar:
                continue
            day_close = bar["close"]
            prev_close = prev_prices.get(ticker)
            if not prev_close or prev_close <= 0:
                continue
            day_return = (day_close - prev_close) / prev_close
            if pos.is_short:
                day_return = -day_return

            # ATR-based threshold: 3x ATR% = "exceptionally unusual move"
            threshold = -0.10  # Fallback if no ATR
            atr_pct = self._atr_cache.get(ticker)
            if atr_pct is not None:
                threshold = -(atr_pct / 100.0) * atr_multiple

            if day_return <= threshold:
                logger.info(
                    "  VOLATILITY: %s dropped %.1f%% ($%.2f → $%.2f) — exceeds %.1fx ATR (threshold %.1f%%)",
                    ticker, day_return * 100, prev_close, day_close,
                    atr_multiple, threshold * 100,
                )
                return True

        # Check portfolio-level (all positions moving against us together)
        if len(self.daily_snapshots) >= 2:
            prev_value = self.daily_snapshots[-2]["portfolio_value"]
            curr_value = self.broker.portfolio_value
            if prev_value > 0:
                portfolio_return = (curr_value - prev_value) / prev_value
                if portfolio_return <= portfolio_threshold:
                    logger.info(
                        "  VOLATILITY: Portfolio dropped %.1f%% in one day ($%s → $%s)",
                        portfolio_return * 100,
                        f"{prev_value:,.0f}",
                        f"{curr_value:,.0f}",
                    )
                    return True

        return False

    def _check_volatility_trigger(
        self, days_since_review: int, threshold: float = 0.05,
    ) -> bool:
        """Check if portfolio has swung 5%+ since the last review.

        Catches sustained high-volatility periods where the world may have
        changed — triggers a calm reassessment, not a panic response.
        Only fires if at least 5 days have passed since last review.
        """
        if len(self.daily_snapshots) <= days_since_review:
            return False

        # Portfolio value at last review
        review_value = self.daily_snapshots[-(days_since_review + 1)]["portfolio_value"]
        current_value = self.broker.portfolio_value
        if review_value <= 0:
            return False

        swing = abs(current_value - review_value) / review_value
        if swing >= threshold:
            direction = "up" if current_value > review_value else "down"
            logger.info(
                "  VOLATILITY: Portfolio swung %.1f%% %s since last review ($%s → $%s)",
                swing * 100, direction,
                f"{review_value:,.0f}", f"{current_value:,.0f}",
            )
            return True
        return False

    def _check_low_vol_trigger(self, hv_threshold: float = 30.0) -> bool:
        """Fire a review when market-wide volatility drops below threshold.

        Uses SPY HV percentile as a proxy for overall options pricing regime.
        Individual high-growth stocks rarely hit low HV percentiles on their own,
        but when the broad market calms down, options on everything become cheaper.
        Only fires once per calm period (requires SPY HV to go back above threshold
        before triggering again).
        """
        if not self.broker.positions:
            return False

        spy_hv = self._spy_hv_pctl
        if spy_hv < hv_threshold:
            # Debounce: only fire once per calm period
            if self._spy_hv_prev < hv_threshold:
                return False  # Already in a calm period
            logger.info(
                "  LOW VOL: SPY HV at %.0fth percentile — market is calm, options premiums are cheap",
                spy_hv,
            )
            self._spy_hv_prev = spy_hv
            return True

        self._spy_hv_prev = spy_hv
        return False

    def _check_catastrophic_stops(self, daily_bars: dict, day_dt: datetime) -> None:
        """Check stops against daily bars.

        For scout positions (mechanical stops), auto-exit and move to watching.
        For core positions (catastrophic 30% stop), trigger an emergency Claude
        review instead of auto-selling — Claude decides EXIT, HOLD, or ADD.
        """
        triggered = self.broker.check_stops_and_targets(daily_bars)
        for t in triggered:
            ticker = t.get("ticker", "")
            pnl = t.get("pnl", 0)
            exit_price = t.get("exit_price", 0)
            reason = t.get("exit_reason", "stopped_out")

            # Determine if this was a scout (mechanical stop) or core (catastrophic)
            pos = self.broker.positions.get(ticker)
            thesis = self.thesis_manager.get_by_ticker(ticker)
            confidence = thesis.get("confidence", "medium") if thesis else "medium"
            is_core = self.risk.is_core_position(confidence)

            if is_core and reason == "stopped_out":
                # Core position hit catastrophic stop — trigger emergency review
                logger.info("")
                logger.info("  !!! CATASTROPHIC STOP HIT: %s @ $%.2f (P&L: $%+.2f)", ticker, exit_price, pnl)
                logger.info("  !!! Triggering emergency Claude review...")

                # Build context for emergency review
                position_data = {
                    "entry_price": pos.entry_price if pos else 0,
                    "current_price": exit_price,
                    "direction": thesis.get("direction", "LONG") if thesis else "LONG",
                }

                # Get technicals for this ticker
                tech_summary = ""
                bars = self._all_bars.get(ticker)
                if bars is not None:
                    history = self._get_bars_up_to(bars, day_dt.date())
                    if not history.empty and len(history) >= 20:
                        snap = self.technicals.analyze(ticker, history)
                        tech_summary = self._format_snapshot(snap)

                # Get recent news
                try:
                    week_start = day_dt - timedelta(days=7)
                    from src.research.world_state import build_world_state
                    world_state = build_world_state(
                        start_date=week_start.strftime("%Y-%m-%d"),
                        end_date=day_dt.strftime("%Y-%m-%d"),
                        holdings=[ticker],
                        watchlist=[ticker],
                        client=self.news_client,
                    )
                except Exception:
                    world_state = "(News unavailable)"

                review = self.decision_engine.run_catastrophic_stop_review(
                    sim_date=str(day_dt.date()),
                    ticker=ticker,
                    position_data=position_data,
                    thesis_data=thesis or {},
                    technicals_summary=tech_summary,
                    world_state=world_state,
                    portfolio_value=self.broker.portfolio_value,
                    cash=self.broker.cash,
                )

                decision = review.get("decision", "EXIT")
                reasoning = review.get("reasoning", "")
                logger.info("  !!! Claude decision: %s — %s", decision, reasoning)

                if decision == "EXIT":
                    # Proceed with the stop-out
                    logger.info("  EXITING %s @ $%.2f (P&L: $%+.2f) — thesis broken", ticker, exit_price, pnl)
                    self.thesis_manager.remove_position(ticker)
                    self.thesis_manager.remove_thesis(ticker)
                elif decision == "HOLD":
                    # Cancel the stop-out, reset catastrophic stop from current price
                    logger.info("  HOLDING %s — resetting catastrophic stop from $%.2f", ticker, exit_price)
                    # Re-add the position (it was already closed by broker.check_stops_and_targets)
                    # We need to re-open it at the exit price
                    from src.strategy.risk_v3 import PositionPlan
                    reopen_plan = PositionPlan(
                        ticker=ticker,
                        quantity=t.get("quantity", 0),
                        entry_price=t.get("entry_price", exit_price),
                        stop_loss=exit_price * 0.70 if not t.get("is_short") else exit_price * 1.30,
                        take_profit=t.get("entry_price", exit_price) * 100.0,
                        risk_amount=0,
                        position_value=t.get("quantity", 0) * exit_price,
                        risk_pct=0,
                        is_short=t.get("is_short", False),
                    )
                    self.broker.place_bracket_order(reopen_plan, is_short=t.get("is_short", False),
                                                     opened_at=t.get("opened_at", day_dt.isoformat()))
                elif decision == "ADD":
                    # Re-open position AND add more
                    add_pct = review.get("add_allocation_pct", 0)
                    logger.info("  ADDING to %s — reopening + deploying %d%% more", ticker, add_pct)
                    import math
                    # Re-open original position
                    from src.strategy.risk_v3 import PositionPlan
                    orig_qty = t.get("quantity", 0)
                    reopen_plan = PositionPlan(
                        ticker=ticker,
                        quantity=orig_qty,
                        entry_price=t.get("entry_price", exit_price),
                        stop_loss=exit_price * 0.70 if not t.get("is_short") else exit_price * 1.30,
                        take_profit=t.get("entry_price", exit_price) * 100.0,
                        risk_amount=0,
                        position_value=orig_qty * exit_price,
                        risk_pct=0,
                        is_short=t.get("is_short", False),
                    )
                    self.broker.place_bracket_order(reopen_plan, is_short=t.get("is_short", False),
                                                     opened_at=t.get("opened_at", day_dt.isoformat()))
                    # Add to position
                    if add_pct > 0 and exit_price > 0:
                        add_value = self.broker.portfolio_value * (add_pct / 100.0)
                        min_cash = self.broker.portfolio_value * self.risk._min_cash_pct
                        available = self.broker.cash - min_cash
                        add_value = min(add_value, max(0, available))
                        add_qty = math.floor(add_value / exit_price)
                        if add_qty > 0:
                            self.broker.add_to_position(ticker, add_qty, exit_price)
                            logger.info("  PYRAMIDED %s: +%d shares @ $%.2f", ticker, add_qty, exit_price)
                continue

            # Scout position or take-profit — standard handling
            logger.info(
                "  STOP HIT: %s @ $%.2f (P&L: $%+.2f) — %s",
                ticker, exit_price, pnl, reason,
            )
            # Update memory — move to watching (thesis may still be valid)
            self.thesis_manager.remove_position(ticker)
            self.thesis_manager.move_to_watching(
                ticker, exit_price=exit_price, reason=reason,
            )

    def _update_ledger_values(self, daily_bars: dict, day=None) -> None:
        """Update current values in the portfolio ledger.

        Uses daily_bars first; falls back to last known bar for positions
        missing from today's data to prevent stale prices in the ledger.
        """
        updates = {}
        for ticker, pos in self.broker.positions.items():
            bar = daily_bars.get(ticker)
            if not bar and day is not None:
                # Forward-fill: use the most recent bar up to today
                bars_df = self._all_bars.get(ticker)
                if bars_df is not None:
                    history = self._get_bars_up_to(bars_df, day)
                    if not history.empty:
                        bar = {
                            "close": float(history["close"].iloc[-1]),
                        }
            if bar:
                updates[ticker] = bar["close"] * pos.quantity

        # Build options snapshot for ledger
        options_data = None
        if self.broker.option_positions:
            options_data = []
            for opt in self.broker.option_positions.values():
                options_data.append({
                    "ticker": opt.ticker,
                    "option_type": opt.option_type,
                    "strike": opt.strike,
                    "expiry": opt.expiry,
                    "quantity": opt.quantity,
                    "premium_paid": opt.premium_paid,
                    "current_premium": opt.current_premium,
                    "is_short": opt.is_short,
                })

        if updates or options_data:
            self.thesis_manager.update_values(updates, options=options_data)

    def _build_technicals_summary(self, day) -> str:
        """Build a technicals summary string for Claude."""
        # Compute SPY snapshot for relative strength calculations
        spy_bars = self._all_bars.get("SPY")
        if spy_bars is not None:
            spy_history = self._get_bars_up_to(spy_bars, day)
            if not spy_history.empty and len(spy_history) >= 20:
                self._spy_snapshot = self.technicals.analyze("SPY", spy_history)
            else:
                self._spy_snapshot = None
        else:
            self._spy_snapshot = None

        lines = []
        # Start with current holdings, then add universe tickers with data
        tickers = list(self.broker.positions.keys())
        for t in self._get_universe_tickers():
            if t not in tickers:
                tickers.append(t)

        self._last_snapshots = {}
        for ticker in tickers:
            bars = self._all_bars.get(ticker)
            if bars is None:
                continue
            history = self._get_bars_up_to(bars, day)
            if history.empty or len(history) < 20:
                continue
            snap = self.technicals.analyze(ticker, history)
            self._last_snapshots[ticker] = snap
            lines.append(self._format_snapshot(snap))

        return "\n".join(lines) if lines else "(No technical data)"

    def _format_snapshot(self, s: TechnicalSnapshot) -> str:
        parts = [f"{s.ticker}: ${s.current_price:.2f}"]
        if s.rsi_14 is not None:
            parts.append(f"RSI={s.rsi_14:.0f}")
        if s.sma_50 is not None:
            trend = "above" if s.current_price > s.sma_50 else "below"
            parts.append(f"{trend} SMA50")
        if s.is_macd_bullish:
            parts.append("MACD bullish")
        elif s.is_macd_bearish:
            parts.append("MACD bearish")
        if s.is_near_lower_band:
            parts.append("near lower BB")
        elif s.is_near_upper_band:
            parts.append("near upper BB")
        if s.hv_20 is not None and s.hv_percentile is not None:
            parts.append(f"HV={s.hv_20:.0f}% ({s.hv_percentile:.0f}th pctl)")
        if s.atr_pct is not None:
            parts.append(f"ATR%={s.atr_pct:.1f}%")
        if s.adx_14 is not None:
            parts.append(f"ADX={s.adx_14:.0f}")
        if s.obv_trend is not None:
            parts.append(f"OBV {s.obv_trend}")
        # Price performance + relative strength vs SPY
        perf_parts = []
        if s.return_1m is not None:
            perf_parts.append(f"1M={s.return_1m:+.1f}%")
        if s.return_3m is not None:
            perf_parts.append(f"3M={s.return_3m:+.1f}%")
        if s.return_6m is not None:
            perf_parts.append(f"6M={s.return_6m:+.1f}%")
        if perf_parts:
            parts.append(f"Returns({', '.join(perf_parts)})")
        # Relative strength vs SPY
        spy_snap = self._spy_snapshot
        if spy_snap and s.return_3m is not None and spy_snap.return_3m is not None:
            rs = s.return_3m - spy_snap.return_3m
            parts.append(f"vs SPY(3M)={rs:+.1f}%")
        return " | ".join(parts)

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_universe_tickers() -> list[str]:
        """Get all tickers from the curated universe config."""
        universe = CONFIG.get("universe", {})
        tickers = []
        seen = set()
        for theme_tickers in universe.values():
            for t in theme_tickers:
                if t not in seen:
                    tickers.append(t)
                    seen.add(t)
        # Fallback to old watchlist if no universe configured
        if not tickers:
            tickers = CONFIG.get("watchlist", {}).get("symbols", [])
        return tickers

    def _download_bars(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        all_bars = {}
        fetch_start = self.start_date - timedelta(days=90)
        for ticker in tickers:
            try:
                bars = self.market.get_bars(
                    ticker, timeframe=TimeFrame.Day,
                    start=fetch_start, end=self.end_date, limit=10000,
                )
                if not bars.empty:
                    all_bars[ticker] = bars
            except Exception as e:
                logger.warning("Failed to download %s: %s", ticker, e)
        return all_bars

    def _get_trading_days(self) -> list:
        all_dates = set()
        for df in self._all_bars.values():
            for idx in df.index:
                dt = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
                if hasattr(dt, 'tzinfo') and dt.tzinfo:
                    dt = dt.replace(tzinfo=None)
                if self.start_date <= dt <= self.end_date:
                    all_dates.add(dt.date())
        return sorted(all_dates)

    def _get_daily_bars(self, day) -> dict[str, dict]:
        result = {}
        for ticker, bars in self._all_bars.items():
            bar = self._get_bar_for_date(bars, day)
            if bar:
                result[ticker] = bar
        return result

    def _get_bar_for_date(self, bars: pd.DataFrame, day) -> dict | None:
        for idx, row in bars.iterrows():
            dt = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
            if hasattr(dt, 'tzinfo') and dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            if dt.date() == day:
                return {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
        return None

    def _get_bars_up_to(self, bars: pd.DataFrame, day) -> pd.DataFrame:
        mask = []
        for idx in bars.index:
            dt = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
            if hasattr(dt, 'tzinfo') and dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            mask.append(dt.date() <= day)
        return bars[mask]

    def _get_price_for_date(self, ticker: str, day_dt: datetime) -> float | None:
        bars = self._all_bars.get(ticker)
        if bars is None:
            return None
        bar = self._get_bar_for_date(bars, day_dt.date())
        return bar["close"] if bar else None

    def _get_or_download_price(self, ticker: str, day_dt: datetime) -> float | None:
        """Try to get price, downloading bars on-demand if needed."""
        # Check if we already have it
        price = self._get_price_for_date(ticker, day_dt)
        if price:
            return price

        # On-demand download for discovered tickers
        if ticker not in self._all_bars:
            logger.info("    Downloading bars for discovered ticker %s...", ticker)
            new_bars = self._download_bars([ticker])
            self._all_bars.update(new_bars)

        return self._get_price_for_date(ticker, day_dt)

    def _get_spy_return(self, day) -> float:
        """Get SPY return from sim start to given day."""
        spy_bars = self._all_bars.get("SPY")
        if spy_bars is None:
            return 0.0
        first_bar = self._get_bar_for_date(spy_bars, self._trading_days[0])
        current_bar = self._get_bar_for_date(spy_bars, day)
        if not first_bar or not current_bar:
            return 0.0
        return ((current_bar["close"] / first_bar["open"]) - 1) * 100

    def _record_snapshot(self, day) -> None:
        self.daily_snapshots.append({
            "date": str(day),
            "portfolio_value": round(self.broker.portfolio_value, 2),
            "cash": round(self.broker.cash, 2),
            "positions": len(self.broker.positions),
            "total_pnl": round(self.broker.total_pnl, 2),
        })

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _build_report(self) -> dict:
        final_value = self.broker.portfolio_value
        total_return = (final_value - self.initial_cash) / self.initial_cash
        num_days = len(self._trading_days)
        annualized = ((1 + total_return) ** (252 / max(num_days, 1))) - 1 if num_days > 0 else 0

        # Max drawdown from snapshots
        peak = self.initial_cash
        max_dd = 0.0
        for snap in self.daily_snapshots:
            pv = snap["portfolio_value"]
            if pv > peak:
                peak = pv
            dd = (peak - pv) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # Win rate from closed trades
        closed = self.broker.closed_trades
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        losses = sum(1 for t in closed if t.get("pnl", 0) <= 0)
        total_trades = len(closed)
        win_rate = wins / total_trades if total_trades > 0 else 0
        total_pnl = sum(t.get("pnl", 0) for t in closed)
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0

        # SPY benchmark
        spy_return_pct = self._get_spy_return(self._trading_days[-1]) if self._trading_days else 0.0
        alpha = total_return * 100 - spy_return_pct

        # Options stats
        options_closed = [t for t in closed if t.get("contract_id")]
        equity_closed = [t for t in closed if not t.get("contract_id")]
        options_pnl = sum(t.get("pnl", 0) for t in options_closed)
        equity_pnl = sum(t.get("pnl", 0) for t in equity_closed)
        open_options = len(self.broker.option_positions)
        equity_val = self.broker.equity_value
        options_val = self.broker.options_value

        report = {
            "version": "V3",
            "period": f"{self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}",
            "start_date": self.start_date.strftime("%Y-%m-%d"),
            "end_date": self.end_date.strftime("%Y-%m-%d"),
            "trading_days": num_days,
            "initial_cash": self.initial_cash,
            "final_value": round(final_value, 2),
            "total_return_pct": round(total_return * 100, 2),
            "annualized_return_pct": round(annualized * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "spy_return_pct": round(spy_return_pct, 2),
            "alpha_pct": round(alpha, 2),
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_trade": round(avg_pnl, 2),
            "open_positions": len(self.broker.positions),
            "open_options": open_options,
            "equity_value": round(equity_val, 2),
            "options_value": round(options_val, 2),
            "equity_trades": len(equity_closed),
            "equity_pnl": round(equity_pnl, 2),
            "options_trades": len(options_closed),
            "options_pnl": round(options_pnl, 2),
            "weekly_reviews": self._weeks_elapsed,
            "review_decisions": self._review_decisions,
            "closed_trades": closed,
        }

        # Print summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("  V3 THESIS SIMULATION RESULTS")
        logger.info("=" * 60)
        logger.info("  Period:              %s", report["period"])
        logger.info("  Trading Days:        %d", report["trading_days"])
        logger.info("  Weekly Reviews:      %d", report["weekly_reviews"])
        logger.info("  Initial Capital:     $%s", f"{report['initial_cash']:,.2f}")
        logger.info("  Final Value:         $%s", f"{report['final_value']:,.2f}")
        if open_options or options_closed:
            logger.info("    Stocks:            $%s (%d open)",
                        f"{equity_val:,.2f}", len(self.broker.positions))
            logger.info("    Options:           $%s (%d open)",
                        f"{options_val:,.2f}", open_options)
            logger.info("    Cash:              $%s", f"{self.broker.cash:,.2f}")
        logger.info("  Total Return:        %+.2f%%", report["total_return_pct"])
        logger.info("  S&P 500 Return:      %+.2f%%", report["spy_return_pct"])
        logger.info("  Alpha:               %+.2f%%", report["alpha_pct"])
        logger.info("  Annualized Return:   %+.2f%%", report["annualized_return_pct"])
        logger.info("  Max Drawdown:        -%.2f%%", report["max_drawdown_pct"])
        logger.info("  Total Trades:        %d (%dW / %dL)", total_trades, wins, losses)
        if options_closed:
            logger.info("    Equity Trades:     %d | P&L: $%s",
                        len(equity_closed), f"{equity_pnl:+,.2f}")
            logger.info("    Options Trades:    %d | P&L: $%s",
                        len(options_closed), f"{options_pnl:+,.2f}")
        logger.info("  Win Rate:            %.1f%%", report["win_rate_pct"])
        logger.info("  Total P&L:           $%s", f"{report['total_pnl']:+,.2f}")
        logger.info("=" * 60)

        return report

