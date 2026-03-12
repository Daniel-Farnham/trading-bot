from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd
from alpaca.data.timeframe import TimeFrame

from src.adaptation.optimizer import StrategyOptimizer
from src.analysis.sentiment import SentimentAnalyzer, SentimentResult
from src.analysis.technical import TechnicalAnalyzer
from src.config import CONFIG, get_alpaca_keys
from src.data.market import MarketData
from src.data.news import NewsFeed, NewsArticle
from src.data.watchlist import Watchlist
from src.simulation.sim_broker import SimBroker
from src.storage.database import Database
from src.storage.models import SentimentRecord, Trade, TradeSide, TradeStatus
from src.strategy.risk import PositionPlan, RiskManager, RiskVeto
from src.strategy.signals import SignalContext, SignalGenerator
from src.strategy.themes import ThemeManager

logger = logging.getLogger(__name__)


class SimulatedSentiment:
    """Generates sentiment from price movement when real news is unavailable.

    Positive price change → positive sentiment
    Negative price change → negative sentiment
    Magnitude scaled by the size of the move.
    """

    def score_from_price_change(
        self, ticker: str, pct_change: float, date_str: str
    ) -> SentimentRecord:
        # Scale: a 3% move maps to ~0.9 sentiment
        score = max(-1.0, min(1.0, pct_change * 30))

        if pct_change > 0:
            headline = f"{ticker} up {pct_change:.1%} — positive market sentiment"
        elif pct_change < 0:
            headline = f"{ticker} down {pct_change:.1%} — negative market sentiment"
        else:
            headline = f"{ticker} flat — neutral market sentiment"

        return SentimentRecord(
            ticker=ticker,
            headline=headline,
            source="price_derived",
            score=round(score, 3),
            timestamp=date_str,
        )


class SimulationEngine:
    """Replays historical market data through the trading bot in compressed time.

    Runs the full trading loop day-by-day over a historical date range,
    using real price data and either real historical news or price-derived sentiment.
    """

    def __init__(
        self,
        start_date: str,
        end_date: str,
        initial_cash: float = 100000.0,
        watchlist: list[str] | None = None,
        news_provider: NewsFeed | None = None,
        use_real_news: bool = True,
        enable_adaptation: bool = False,
        review_interval_days: int = 5,
        db_path: str | None = None,
        on_day_complete: Callable | None = None,
    ):
        self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = datetime.strptime(end_date, "%Y-%m-%d")
        self.initial_cash = initial_cash
        self.enable_adaptation = enable_adaptation
        self.review_interval_days = review_interval_days
        self.use_real_news = use_real_news
        self.on_day_complete = on_day_complete

        # Components
        symbols = watchlist or CONFIG.get("watchlist", {}).get("symbols", [])[:10]
        self.watchlist = Watchlist(symbols=symbols)
        self.market = MarketData()
        self.news = news_provider or NewsFeed()
        self.sim_sentiment = SimulatedSentiment()
        self.technicals = TechnicalAnalyzer()
        self.themes = ThemeManager()
        self.signals = SignalGenerator(theme_manager=self.themes)
        self.risk = RiskManager()
        self.broker = SimBroker(initial_cash=initial_cash)

        # Database for logging
        if db_path:
            self._db_path = Path(db_path)
        else:
            self._db_path = Path("data/simulation.db")
        self.db = Database(self._db_path)

        # Adaptation layer
        self.optimizer: StrategyOptimizer | None = None  # Initialized after db.connect()
        self.adaptation_results: list[dict] = []

        # Track results
        self.daily_snapshots: list[dict] = []
        self._peak_value = initial_cash
        self._days_since_review = 0

    def run(self) -> dict:
        """Run the full simulation. Returns performance report."""
        logger.info(
            "Starting simulation: %s to %s | Cash: $%.0f | Watchlist: %s",
            self.start_date.strftime("%Y-%m-%d"),
            self.end_date.strftime("%Y-%m-%d"),
            self.initial_cash,
            ", ".join(self.watchlist.symbols),
        )

        self.db.connect()
        self.optimizer = StrategyOptimizer(self.db)

        # Seed initial strategy params in DB from config
        trading_cfg = CONFIG.get("trading", {})
        for key in [
            "sentiment_buy_threshold", "sentiment_sell_threshold",
            "rsi_overbought", "atr_stop_loss_multiplier",
            "atr_take_profit_multiplier", "max_position_pct",
        ]:
            if key in trading_cfg:
                self.db.set_param(key, float(trading_cfg[key]))

        # Pre-download historical bars for all tickers
        logger.info("Downloading historical data...")
        all_bars = self._download_all_bars()
        logger.info("Historical data downloaded for %d tickers.", len(all_bars))

        # Get trading days from the data
        trading_days = self._get_trading_days(all_bars)
        logger.info("Simulating %d trading days.", len(trading_days))

        # Run each trading day
        for i, day in enumerate(trading_days):
            self._simulate_day(day, all_bars, i)
            self._days_since_review += 1

            # Run adaptation review every N days (only if enabled)
            if self.enable_adaptation and self._days_since_review >= self.review_interval_days:
                self._run_adaptation_review(day)
                self._days_since_review = 0

            if self.on_day_complete:
                self.on_day_complete(day, self.daily_snapshots[-1])

            # Progress logging every 20 days
            if (i + 1) % 20 == 0:
                pv = self.broker.portfolio_value
                logger.info(
                    "Day %d/%d (%s) — Portfolio: $%.2f (%.1f%%)",
                    i + 1, len(trading_days), day.strftime("%Y-%m-%d"),
                    pv, ((pv / self.initial_cash) - 1) * 100,
                )

        report = self._build_report(trading_days)
        self.db.close()

        return report

    def _download_all_bars(self) -> dict[str, pd.DataFrame]:
        """Download historical daily bars for all watchlist tickers."""
        all_bars = {}
        # Fetch extra history before start_date for technical indicators
        fetch_start = self.start_date - timedelta(days=90)

        for ticker in self.watchlist:
            try:
                bars = self.market.get_bars(
                    ticker,
                    timeframe=TimeFrame.Day,
                    start=fetch_start,
                    end=self.end_date,
                    limit=10000,
                )
                if not bars.empty:
                    all_bars[ticker] = bars
                    logger.debug("Downloaded %d bars for %s", len(bars), ticker)
            except Exception as e:
                logger.warning("Failed to download bars for %s: %s", ticker, e)

        return all_bars

    def _get_trading_days(self, all_bars: dict[str, pd.DataFrame]) -> list[datetime]:
        """Extract unique trading days from the data within our date range."""
        all_dates = set()
        for df in all_bars.values():
            for idx in df.index:
                dt = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
                if hasattr(dt, 'tzinfo') and dt.tzinfo:
                    dt = dt.replace(tzinfo=None)
                if self.start_date <= dt <= self.end_date:
                    all_dates.add(dt.date())

        return sorted(all_dates)

    def _simulate_day(
        self, day, all_bars: dict[str, pd.DataFrame], day_index: int
    ) -> None:
        """Simulate one trading day."""
        day_dt = datetime.combine(day, datetime.min.time())
        daily_bar_data = {}
        day_actions: list[str] = []  # Track actions for daily summary

        # Check circuit breakers
        pv = self.broker.portfolio_value
        if pv > self._peak_value:
            self._peak_value = pv

        if not self.risk.check_drawdown(pv, self._peak_value):
            logger.warning("Day %s: Max drawdown hit. Skipping.", day)
            self._record_snapshot(day, skipped="drawdown")
            return

        # Check stops and targets against today's price action
        for ticker in self.watchlist:
            bars = all_bars.get(ticker)
            if bars is None:
                continue
            day_bar = self._get_bar_for_date(bars, day)
            if day_bar is not None:
                daily_bar_data[ticker] = day_bar

        triggered = self.broker.check_stops_and_targets(daily_bar_data)
        for t in triggered:
            self._log_closed_trade(t, day_dt)
            reason = t.get("exit_reason", "closed")
            pnl = t.get("pnl", 0)
            sign = "+" if pnl >= 0 else ""
            day_actions.append(
                f"CLOSED {t['ticker']} ({reason}) @ ${t.get('exit_price', 0):.2f} → P&L: {sign}${pnl:.2f}"
            )

        # Evaluate each ticker for new signals
        account = self.broker.get_account_snapshot()
        positions = self.broker.get_positions_list()
        position_tickers = [p["ticker"] for p in positions]

        for ticker in self.watchlist:
            if ticker in position_tickers:
                continue

            bars = all_bars.get(ticker)
            if bars is None:
                continue

            # Get bars up to this day for technicals
            history = self._get_bars_up_to(bars, day)
            if history.empty or len(history) < 20:
                continue

            # Get sentiment
            sentiment_records = self._get_sentiment_for_day(ticker, day, history)
            if not sentiment_records:
                continue

            avg_sentiment = sum(r.score for r in sentiment_records) / len(sentiment_records)

            # Technical analysis
            snapshot = self.technicals.analyze(ticker, history)

            # Generate signal
            ctx = SignalContext(
                ticker=ticker,
                sentiment_records=sentiment_records,
                technicals=snapshot,
                avg_sentiment=avg_sentiment,
            )

            signal = self.signals.evaluate(ctx)
            if signal is None or signal.side != TradeSide.BUY:
                continue

            # Risk check
            plan = self.risk.evaluate(
                signal=signal,
                portfolio_value=account["portfolio_value"],
                cash=account["cash"],
                open_position_count=len(positions),
                existing_ticker_positions=position_tickers,
            )

            if isinstance(plan, RiskVeto):
                continue

            # Execute
            result = self.broker.place_bracket_order(plan)
            if result.success:
                day_actions.append(
                    f"BOUGHT {plan.quantity} {ticker} @ ${plan.entry_price:.2f} "
                    f"(SL: ${plan.stop_loss:.2f}, TP: ${plan.take_profit:.2f}, "
                    f"confidence: {signal.confidence:.0%})"
                )
                trade = Trade(
                    ticker=ticker,
                    side=TradeSide.BUY,
                    quantity=plan.quantity,
                    entry_price=plan.entry_price,
                    stop_loss=plan.stop_loss,
                    take_profit=plan.take_profit,
                    sentiment_score=signal.sentiment_score,
                    confidence=signal.confidence,
                    reasoning=signal.reasoning,
                    opened_at=day_dt.isoformat(),
                )
                self.db.insert_trade(trade)
                for r in sentiment_records:
                    self.db.insert_sentiment(r)

                # Update positions list for remaining tickers
                positions = self.broker.get_positions_list()
                position_tickers = [p["ticker"] for p in positions]

        # Check exits on negative sentiment for existing positions
        for ticker in list(position_tickers):
            bars = all_bars.get(ticker)
            if bars is None:
                continue
            history = self._get_bars_up_to(bars, day)
            if history.empty:
                continue

            sentiment_records = self._get_sentiment_for_day(ticker, day, history)
            if not sentiment_records:
                continue

            avg_sent = sum(r.score for r in sentiment_records) / len(sentiment_records)
            sell_threshold = CONFIG.get("trading", {}).get("sentiment_sell_threshold", -0.4)

            if avg_sent < sell_threshold:
                bar = daily_bar_data.get(ticker)
                price = bar["close"] if bar else None
                if price:
                    result = self.broker.close_position(ticker, price)
                    if result.success:
                        closed = self.broker.closed_trades[-1]
                        pnl = closed.get("pnl", 0)
                        sign = "+" if pnl >= 0 else ""
                        day_actions.append(
                            f"SOLD {ticker} (sentiment_exit) @ ${price:.2f} → P&L: {sign}${pnl:.2f}"
                        )
                    self._log_closed_trade(
                        {"ticker": ticker, "exit_reason": "sentiment_exit"}, day_dt
                    )

        # Daily summary log
        pv = self.broker.portfolio_value
        ret_pct = ((pv / self.initial_cash) - 1) * 100
        pos_count = len(self.broker.positions)
        if day_actions:
            actions_str = " | ".join(day_actions)
            logger.info(
                "Day %d (%s): %s — Portfolio: $%.2f (%.1f%%) — Positions: %d",
                day_index + 1, day, actions_str, pv, ret_pct, pos_count,
            )
        else:
            logger.debug(
                "Day %d (%s): No actions — Portfolio: $%.2f (%.1f%%) — Positions: %d",
                day_index + 1, day, pv, ret_pct, pos_count,
            )

        self._record_snapshot(day)

    def _run_adaptation_review(self, day) -> None:
        """Run the Claude-powered adaptation review during simulation."""
        stats = self.db.get_trade_stats()
        if stats["total"] < 3:
            return

        since = (datetime.combine(day, datetime.min.time()) - timedelta(days=7)).isoformat() \
            if not isinstance(day, datetime) else (day - timedelta(days=7)).isoformat()
        recent_trades = self.db.get_trades_since(since)
        current_params = self.db.get_all_params()

        logger.info(
            "Day %s: Running adaptation review (trades: %d, win rate: %.0f%%)",
            day, stats["total"], stats["win_rate"] * 100,
        )

        result = self.optimizer.run_simulation_review(stats, recent_trades, current_params)
        self.adaptation_results.append({
            "date": str(day),
            "stats": stats,
            "result": result,
        })

        # Apply updated params to the signal generator
        if result.get("changes"):
            updated_params = self.db.get_all_params()
            self.signals = SignalGenerator(
                theme_manager=self.themes,
                params={**CONFIG.get("trading", {}), **{k: v for k, v in updated_params.items()}},
            )
            logger.info(
                "Strategy adapted: %d parameter(s) changed",
                len(result["changes"]),
            )

    def _get_sentiment_for_day(
        self, ticker: str, day, history: pd.DataFrame
    ) -> list[SentimentRecord]:
        """Get sentiment records for a ticker on a given day."""
        day_str = day.isoformat() if hasattr(day, 'isoformat') else str(day)

        # Try real news first
        if self.use_real_news:
            try:
                day_dt = datetime.combine(day, datetime.min.time()) if not isinstance(day, datetime) else day
                articles = self.news.fetch_news(
                    ticker, limit=5,
                    start=day_dt,
                    end=day_dt + timedelta(days=1),
                )
                if articles:
                    # Use price-derived sentiment as a proxy for FinBERT
                    # (avoids loading the model during simulation)
                    records = []
                    for a in articles:
                        pct = self._get_day_return(history, day)
                        r = self.sim_sentiment.score_from_price_change(ticker, pct, day_str)
                        r.headline = a.headline
                        r.source = a.source
                        records.append(r)
                    return records
            except Exception:
                pass

        # Fallback: derive sentiment from price movement
        pct_change = self._get_day_return(history, day)
        record = self.sim_sentiment.score_from_price_change(ticker, pct_change, day_str)
        return [record]

    def _get_day_return(self, history: pd.DataFrame, day) -> float:
        """Calculate the percentage return for a specific day."""
        if len(history) < 2:
            return 0.0
        try:
            today_close = float(history["close"].iloc[-1])
            yesterday_close = float(history["close"].iloc[-2])
            if yesterday_close == 0:
                return 0.0
            return (today_close - yesterday_close) / yesterday_close
        except (IndexError, KeyError):
            return 0.0

    def _get_bar_for_date(self, bars: pd.DataFrame, day) -> dict | None:
        """Get the OHLCV bar for a specific date."""
        for idx, row in bars.iterrows():
            dt = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
            if hasattr(dt, 'tzinfo') and dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            if dt.date() == day if hasattr(day, 'year') else dt.date() == day:
                return {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
        return None

    def _get_bars_up_to(self, bars: pd.DataFrame, day) -> pd.DataFrame:
        """Get all bars up to and including a specific date."""
        mask = []
        for idx in bars.index:
            dt = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
            if hasattr(dt, 'tzinfo') and dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            dt_date = dt.date() if hasattr(dt, 'date') else dt
            day_date = day if not hasattr(day, 'date') else (day.date() if hasattr(day, 'date') else day)
            mask.append(dt_date <= day_date)
        return bars[mask]

    def _log_closed_trade(self, trade_data: dict, day_dt: datetime) -> None:
        """Log a closed trade to the database."""
        ticker = trade_data.get("ticker", "")
        open_trades = self.db.get_trades_by_ticker(ticker)
        for t in open_trades:
            if t["status"] == "open":
                exit_price = trade_data.get("exit_price", trade_data.get("take_profit", 0))
                pnl = trade_data.get("pnl", 0)
                reason = trade_data.get("exit_reason", "closed")
                status = TradeStatus.STOPPED_OUT if reason == "stopped_out" else TradeStatus.CLOSED

                self.db.close_trade(
                    trade_id=t["id"],
                    exit_price=exit_price,
                    status=status,
                    pnl=pnl,
                    closed_at=day_dt.isoformat(),
                )
                break

    def _record_snapshot(self, day, skipped: str | None = None) -> None:
        """Record end-of-day portfolio snapshot."""
        self.daily_snapshots.append({
            "date": str(day),
            "portfolio_value": round(self.broker.portfolio_value, 2),
            "cash": round(self.broker.cash, 2),
            "positions": len(self.broker.positions),
            "total_pnl": round(self.broker.total_pnl, 2),
            "skipped": skipped,
        })

    def _build_report(self, trading_days: list) -> dict:
        """Build final simulation performance report."""
        final_value = self.broker.portfolio_value
        total_return = (final_value - self.initial_cash) / self.initial_cash
        num_days = len(trading_days)
        annualized_return = ((1 + total_return) ** (252 / max(num_days, 1))) - 1 if num_days > 0 else 0

        stats = self.db.get_trade_stats()

        # Calculate max drawdown from snapshots
        peak = self.initial_cash
        max_dd = 0.0
        for snap in self.daily_snapshots:
            pv = snap["portfolio_value"]
            if pv > peak:
                peak = pv
            dd = (peak - pv) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        report = {
            "period": f"{self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}",
            "trading_days": num_days,
            "initial_cash": self.initial_cash,
            "final_value": round(final_value, 2),
            "total_return_pct": round(total_return * 100, 2),
            "annualized_return_pct": round(annualized_return * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "total_trades": stats["total"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate_pct": round(stats["win_rate"] * 100, 1),
            "total_pnl": round(stats.get("total_pnl", 0), 2),
            "avg_pnl_per_trade": round(stats["avg_pnl"], 2),
            "open_positions": len(self.broker.positions),
            "adaptation_reviews": len(self.adaptation_results),
            "adaptations": self.adaptation_results,
            "closed_trades": self.broker.closed_trades,
        }

        # Print summary
        logger.info("=" * 60)
        logger.info("SIMULATION RESULTS")
        logger.info("=" * 60)
        logger.info("Period:              %s", report["period"])
        logger.info("Trading Days:        %d", report["trading_days"])
        logger.info("Initial Capital:     $%.2f", report["initial_cash"])
        logger.info("Final Value:         $%.2f", report["final_value"])
        logger.info("Total Return:        %.2f%%", report["total_return_pct"])
        logger.info("Annualized Return:   %.2f%%", report["annualized_return_pct"])
        logger.info("Max Drawdown:        %.2f%%", report["max_drawdown_pct"])
        logger.info("Total Trades:        %d", report["total_trades"])
        logger.info("Win Rate:            %.1f%%", report["win_rate_pct"])
        logger.info("Total P&L:           $%.2f", report["total_pnl"])
        logger.info("Avg P&L/Trade:       $%.2f", report["avg_pnl_per_trade"])
        logger.info("=" * 60)

        return report
