"""V3 Thesis-Driven Simulation Engine.

Steps through historical dates week by week, orchestrating the full
research → decision → execution loop. Memory files persist and evolve
across sim weeks, giving Claude continuity.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from alpaca.data.timeframe import TimeFrame

from src.analysis.technical import TechnicalAnalyzer, TechnicalSnapshot
from src.config import CONFIG
from src.data.market import MarketData
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
        review_cadence_days: int = 5,
        monthly_review_cadence_days: int = 20,
        data_dir: str | Path | None = None,
    ):
        self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = datetime.strptime(end_date, "%Y-%m-%d")
        self.initial_cash = initial_cash
        self.review_cadence = review_cadence_days
        self.monthly_cadence = monthly_review_cadence_days

        # Data directory for memory files
        self._data_dir = Path(data_dir) if data_dir else Path("data/v3_sim")
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Components
        self.market = MarketData()
        self.news_client = AlpacaNewsClient()
        self.technicals = TechnicalAnalyzer()
        self.risk = RiskManagerV3()
        self.broker = SimBroker(initial_cash=initial_cash)

        # Memory system — isolated to sim data dir, except sim_log which persists
        self.thesis_manager = ThesisManager(base_dir=self._data_dir)
        # Override paths: in-sim files go to data_dir, sim_log stays at project root
        self.thesis_manager._paths = {
            "theses": self._data_dir / "active_theses.md",
            "ledger": self._data_dir / "portfolio_ledger.md",
            "summaries": self._data_dir / "quarterly_summaries.md",
            "lessons": self._data_dir / "lessons_learned.md",
            "sim_log": Path("data/simulation_log.md"),
            "themes": self._data_dir / "themes.md",
        }

        self.decision_engine = DecisionEngine(thesis_manager=self.thesis_manager)

        # Tracking
        self.daily_snapshots: list[dict] = []
        self._peak_value = initial_cash
        self._all_bars: dict[str, pd.DataFrame] = {}
        self._trading_days: list = []
        self._review_decisions: list[dict] = []
        self._weeks_elapsed = 0

    def run(self) -> dict:
        """Run the full thesis-driven simulation."""
        logger.info(
            "V3 Thesis Simulation: %s to %s | Cash: $%s",
            self.start_date.strftime("%Y-%m-%d"),
            self.end_date.strftime("%Y-%m-%d"),
            f"{self.initial_cash:,.0f}",
        )

        # Clear previous sim memory (preserves themes and sim_log)
        self.thesis_manager.clear_all()

        # Seed initial themes if none exist
        if not self.thesis_manager.get_all_themes():
            default_themes = [
                ("AI/Automation", "Companies building or benefiting from AI, robotics, automation"),
                ("Climate Transition", "Clean energy, EVs, sustainability, grid infrastructure"),
                ("Aging Populations", "Healthcare, pharma, medical devices, senior services"),
                ("Wealth Inequality", "Financial services, fintech, discount retail, luxury"),
            ]
            for name, desc in default_themes:
                self.thesis_manager.add_theme(name, desc, score=3)
            logger.info("Seeded %d initial themes", len(default_themes))

        # Download historical data for the full curated universe
        universe = self._get_universe_tickers()
        logger.info("Downloading historical data for %d tickers...", len(universe))
        self._all_bars = self._download_bars(universe)
        self._trading_days = self._get_trading_days()
        logger.info("Got %d trading days across %d tickers.", len(self._trading_days), len(self._all_bars))

        # Main simulation loop — step day by day, review every N days
        days_since_review = self.review_cadence  # Force review on first day
        days_since_monthly = 0

        for i, day in enumerate(self._trading_days):
            day_dt = datetime.combine(day, datetime.min.time())

            # Daily: update prices, check catastrophic stops
            daily_bars = self._get_daily_bars(day)
            self.broker.update_prices(daily_bars)
            self._check_catastrophic_stops(daily_bars, day_dt)
            self._update_ledger_values(daily_bars)
            self._record_snapshot(day)

            # Track peak for drawdown
            pv = self.broker.portfolio_value
            if pv > self._peak_value:
                self._peak_value = pv

            days_since_review += 1
            days_since_monthly += 1

            # Weekly review
            if days_since_review >= self.review_cadence:
                if not self.risk.check_drawdown(pv, self._peak_value):
                    dd = (self._peak_value - pv) / self._peak_value * 100
                    logger.warning("  Drawdown %.1f%% — skipping review, stops still active", dd)
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
                logger.info(
                    ">>> Day %d/%d (%s) | $%s (%+.1f%%) | %d positions",
                    i + 1, len(self._trading_days), day, f"{pv:,.0f}", ret, pos_count,
                )

        report = self._build_report()
        return report

    def _run_review(self, day, day_dt: datetime, review_type: str) -> None:
        """Execute a weekly/monthly thesis review."""
        label = review_type.upper()
        logger.info("")
        logger.info("=" * 60)
        logger.info("  %s REVIEW | %s", label, day)
        logger.info(
            "  Portfolio: $%s | Cash: $%s | Positions: %d",
            f"{self.broker.portfolio_value:,.0f}", f"{self.broker.cash:,.0f}",
            len(self.broker.positions),
        )
        logger.info("=" * 60)

        # Step 1: Research — build world state from Alpaca News
        week_start = day_dt - timedelta(days=self.review_cadence)
        try:
            holdings_tickers = [h["ticker"] for h in self.thesis_manager.get_holdings()]
            universe_tickers = self._get_universe_tickers()
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

        # Step 3: Claude review
        logger.info("  Calling Claude for %s review...", review_type)
        response = self.decision_engine.run_weekly_review(
            sim_date=str(day),
            world_state=world_state,
            technicals_summary=technicals_summary,
            portfolio_value=self.broker.portfolio_value,
            cash=self.broker.cash,
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
            + len(response.get("reduce_positions", []))
        )
        logger.info("  Review complete: %d action(s)", changes)

    def _execute_decisions(self, response: dict, daily_bars: dict, day_dt: datetime) -> None:
        """Translate Claude's decisions into simulated trades."""
        positions = self.broker.get_positions_list()
        position_tickers = [p["ticker"] for p in positions]

        # Close positions
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
                    logger.info("    SOLD %s @ $%.2f — %s", ticker, price, close.get("reason", ""))

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

        # New positions
        for new_pos in response.get("new_positions", []):
            ticker = new_pos.get("ticker", "")
            if not ticker or ticker in position_tickers:
                continue

            bar = daily_bars.get(ticker)
            if not bar:
                # On-demand: download bars for a newly discovered ticker
                price = self._get_or_download_price(ticker, day_dt)
                if not price:
                    logger.warning("    No price data for %s, skipping", ticker)
                    continue
            else:
                price = bar["close"]

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
            )

            if isinstance(plan, V3RiskVeto):
                logger.info("    VETOED %s: %s", ticker, plan.reason)
                continue

            # Convert to SimBroker-compatible plan
            from src.strategy.risk_v3 import PositionPlan
            sim_plan = PositionPlan(
                ticker=plan.ticker,
                quantity=plan.quantity,
                entry_price=plan.entry_price,
                stop_loss=plan.catastrophic_stop,
                take_profit=plan.entry_price * 2,  # Thesis-driven exit, not target
                risk_amount=0,
                position_value=plan.position_value,
                risk_pct=0,
                is_short=(plan.side == "SHORT"),
            )

            is_short = plan.side == "SHORT"
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
                logger.info(
                    "    %s %d %s @ $%.2f (%s%% alloc, stop $%.2f)",
                    action, plan.quantity, ticker, price,
                    plan.allocation_pct, plan.catastrophic_stop,
                )

    def _check_catastrophic_stops(self, daily_bars: dict, day_dt: datetime) -> None:
        """Check wide catastrophic stops against daily bars."""
        triggered = self.broker.check_stops_and_targets(daily_bars)
        for t in triggered:
            ticker = t.get("ticker", "")
            pnl = t.get("pnl", 0)
            reason = t.get("exit_reason", "stopped_out")
            logger.info(
                "  STOP HIT: %s @ $%.2f (P&L: $%+.2f) — %s",
                ticker, t.get("exit_price", 0), pnl, reason,
            )
            # Update memory
            self.thesis_manager.remove_position(ticker)
            self.thesis_manager.update_thesis(ticker, status="STOPPED_OUT")

    def _update_ledger_values(self, daily_bars: dict) -> None:
        """Update current values in the portfolio ledger."""
        updates = {}
        for ticker, pos in self.broker.positions.items():
            bar = daily_bars.get(ticker)
            if bar:
                updates[ticker] = bar["close"] * pos.quantity
        if updates:
            self.thesis_manager.update_values(updates)

    def _build_technicals_summary(self, day) -> str:
        """Build a technicals summary string for Claude."""
        lines = []
        # Start with current holdings, then add universe tickers with data
        tickers = list(self.broker.positions.keys())
        for t in self._get_universe_tickers():
            if t not in tickers:
                tickers.append(t)

        for ticker in tickers:
            bars = self._all_bars.get(ticker)
            if bars is None:
                continue
            history = self._get_bars_up_to(bars, day)
            if history.empty or len(history) < 20:
                continue
            snap = self.technicals.analyze(ticker, history)
            lines.append(self._format_snapshot(snap))

        return "\n".join(lines) if lines else "(No technical data)"

    @staticmethod
    def _format_snapshot(s: TechnicalSnapshot) -> str:
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

        report = {
            "version": "V3",
            "period": f"{self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}",
            "trading_days": num_days,
            "initial_cash": self.initial_cash,
            "final_value": round(final_value, 2),
            "total_return_pct": round(total_return * 100, 2),
            "annualized_return_pct": round(annualized * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_trade": round(avg_pnl, 2),
            "open_positions": len(self.broker.positions),
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
        logger.info("  Total Return:        %+.2f%%", report["total_return_pct"])
        logger.info("  Annualized Return:   %+.2f%%", report["annualized_return_pct"])
        logger.info("  Max Drawdown:        -%.2f%%", report["max_drawdown_pct"])
        logger.info("  Total Trades:        %d (%dW / %dL)", total_trades, wins, losses)
        logger.info("  Win Rate:            %.1f%%", report["win_rate_pct"])
        logger.info("  Total P&L:           $%s", f"{report['total_pnl']:+,.2f}")
        logger.info("=" * 60)

        return report

    def append_to_sim_log(self, report: dict, notes: str = "") -> None:
        """Append this run's results to the simulation log with strategy insights."""
        run_id = datetime.utcnow().strftime("%Y-%m-%d_%H%M")
        body = (
            f"**Date Run:** {datetime.utcnow().strftime('%Y-%m-%d')} | "
            f"**Architecture:** V3 | **Initial Cash:** ${report['initial_cash']:,.0f}\n\n"
            f"**Results:**\n"
            f"- Final Value: ${report['final_value']:,.2f} ({report['total_return_pct']:+.1f}%)\n"
            f"- Total Trades: {report['total_trades']} ({report['wins']}W / {report['losses']}L)\n"
            f"- Win Rate: {report['win_rate_pct']:.1f}%\n"
            f"- Max Drawdown: -{report['max_drawdown_pct']:.1f}%\n"
            f"- Weekly Reviews: {report['weekly_reviews']}\n"
        )

        # Add per-ticker performance
        closed = report.get("closed_trades", [])
        if closed:
            ticker_pnl: dict[str, float] = defaultdict(float)
            ticker_count: dict[str, int] = defaultdict(int)
            for t in closed:
                ticker_pnl[t["ticker"]] += t.get("pnl", 0)
                ticker_count[t["ticker"]] += 1
            body += "\n**Per-Ticker Performance:**\n"
            for tk in sorted(ticker_pnl, key=lambda x: -ticker_pnl[x]):
                body += f"- {tk}: ${ticker_pnl[tk]:+,.2f} ({ticker_count[tk]} trades)\n"

        # Add active theses at end of sim
        theses = self.thesis_manager.get_all_theses()
        if theses:
            active = [t for t in theses if t.get("status", "").upper() == "ACTIVE"]
            if active:
                body += "\n**Active Theses at End:**\n"
                for t in active:
                    body += f"- {t['ticker']} ({t['direction']}): {t.get('thesis', '')[:80]}...\n"

        # Add key lessons learned
        lessons = self.thesis_manager.get_all_lessons()
        if lessons:
            body += f"\n**Lessons Learned ({len(lessons)} total):**\n"
            # Include last 5 lessons as they're the most refined
            for lesson in lessons[-5:]:
                # Extract just the content after "## Lesson N\n"
                lines = lesson.split("\n", 1)
                content = lines[1].strip() if len(lines) > 1 else lines[0]
                body += f"- {content[:120]}...\n" if len(content) > 120 else f"- {content}\n"

        if notes:
            body += f"\n**Notes:**\n{notes}\n"

        self.thesis_manager.append_sim_run(run_id, body)
