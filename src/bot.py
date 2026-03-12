from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from src.analysis.sentiment import SentimentAnalyzer
from src.analysis.technical import TechnicalAnalyzer
from src.config import CONFIG
from src.data.market import MarketData
from src.data.news import NewsFeed
from src.data.watchlist import Watchlist
from src.execution.broker import Broker
from src.storage.database import Database
from src.storage.models import Trade, TradeSide, TradeStatus
from src.strategy.risk import PositionPlan, RiskManager, RiskVeto
from src.strategy.signals import SignalContext, SignalGenerator
from src.strategy.themes import ThemeManager

logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(
        self,
        market: MarketData | None = None,
        news: NewsFeed | None = None,
        sentiment: SentimentAnalyzer | None = None,
        technicals: TechnicalAnalyzer | None = None,
        signals: SignalGenerator | None = None,
        risk: RiskManager | None = None,
        broker: Broker | None = None,
        themes: ThemeManager | None = None,
        db: Database | None = None,
        watchlist: Watchlist | None = None,
    ):
        self.market = market or MarketData()
        self.news = news or NewsFeed()
        self.sentiment = sentiment or SentimentAnalyzer()
        self.technicals = technicals or TechnicalAnalyzer()
        self.themes = themes or ThemeManager()
        self.signals = signals or SignalGenerator(theme_manager=self.themes)
        self.risk = risk or RiskManager()
        self.broker = broker or Broker()
        self.watchlist = watchlist or Watchlist()

        if db is not None:
            self.db = db
        else:
            db_path = Path(__file__).parent.parent / CONFIG["database"]["path"]
            self.db = Database(db_path)
            self.db.connect()

        self._peak_value: float = 0.0

    def run_cycle(self) -> dict:
        """Runs one full trading cycle. Returns a summary of actions taken."""
        summary = {
            "timestamp": datetime.utcnow().isoformat(),
            "signals_generated": 0,
            "trades_placed": 0,
            "trades_vetoed": 0,
            "errors": [],
        }

        # Step 1: Check if market is open
        try:
            if not self.market.is_market_open():
                logger.info("Market is closed. Skipping cycle.")
                summary["skipped"] = "market_closed"
                return summary
        except Exception as e:
            logger.error("Failed to check market status: %s", e)
            summary["errors"].append(f"Market status check failed: {e}")
            return summary

        # Step 2: Get account state and check circuit breakers
        try:
            account = self.market.get_account()
            portfolio_value = account["portfolio_value"]
            cash = account["cash"]

            if portfolio_value > self._peak_value:
                self._peak_value = portfolio_value

            if not self.risk.check_drawdown(portfolio_value, self._peak_value):
                logger.warning("Max drawdown breached. Pausing trading.")
                summary["skipped"] = "max_drawdown"
                return summary

        except Exception as e:
            logger.error("Failed to get account: %s", e)
            summary["errors"].append(f"Account fetch failed: {e}")
            return summary

        # Step 3: Get current positions
        try:
            positions = self.market.get_positions()
            position_tickers = [p["ticker"] for p in positions]
        except Exception as e:
            logger.error("Failed to get positions: %s", e)
            summary["errors"].append(f"Positions fetch failed: {e}")
            return summary

        # Step 4: Evaluate each watchlist ticker
        for ticker in self.watchlist:
            try:
                result = self._evaluate_ticker(
                    ticker, portfolio_value, cash,
                    len(positions), position_tickers,
                )
                if result == "signal":
                    summary["signals_generated"] += 1
                    summary["trades_placed"] += 1
                elif result == "vetoed":
                    summary["signals_generated"] += 1
                    summary["trades_vetoed"] += 1
            except Exception as e:
                logger.error("Error evaluating %s: %s", ticker, e)
                summary["errors"].append(f"{ticker}: {e}")

        # Step 5: Check existing positions for exit signals
        for pos in positions:
            try:
                self._check_exit(pos)
            except Exception as e:
                logger.error("Error checking exit for %s: %s", pos["ticker"], e)
                summary["errors"].append(f"Exit check {pos['ticker']}: {e}")

        logger.info(
            "Cycle complete: %d signals, %d trades, %d vetoed, %d errors",
            summary["signals_generated"],
            summary["trades_placed"],
            summary["trades_vetoed"],
            len(summary["errors"]),
        )

        return summary

    def _evaluate_ticker(
        self,
        ticker: str,
        portfolio_value: float,
        cash: float,
        open_position_count: int,
        position_tickers: list[str],
    ) -> str | None:
        """Evaluate a single ticker for trading opportunity.

        Returns: 'signal' if trade placed, 'vetoed' if signal rejected, None otherwise.
        """
        # Fetch news
        start = datetime.utcnow() - timedelta(hours=24)
        articles = self.news.fetch_news(ticker, limit=10, start=start)

        if not articles:
            return None

        # Score sentiment
        records = self.sentiment.score_articles(articles)
        avg_sentiment = self.sentiment.aggregate_sentiment(records)

        # Log sentiment to database
        for record in records:
            self.db.insert_sentiment(record)

        # Get technical indicators
        bars = self.market.get_bars(ticker, limit=60)
        snapshot = self.technicals.analyze(ticker, bars)

        # Build signal context
        ctx = SignalContext(
            ticker=ticker,
            sentiment_records=records,
            technicals=snapshot,
            avg_sentiment=avg_sentiment,
        )

        # Generate signal
        signal = self.signals.evaluate(ctx)
        if signal is None:
            return None

        # Only execute buy signals from the scan loop
        # Sell signals are handled in _check_exit
        if signal.side != TradeSide.BUY:
            return None

        # Risk check
        plan = self.risk.evaluate(
            signal=signal,
            portfolio_value=portfolio_value,
            cash=cash,
            open_position_count=open_position_count,
            existing_ticker_positions=position_tickers,
        )

        if isinstance(plan, RiskVeto):
            logger.info(
                "Signal for %s vetoed: %s", ticker, plan.reason,
            )
            return "vetoed"

        # Execute trade
        order_result = self.broker.place_bracket_order(plan)

        if order_result.success:
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
            )
            self.db.insert_trade(trade)
            logger.info(
                "Trade placed: BUY %d %s @ $%.2f (conf: %.2f)",
                plan.quantity, ticker, plan.entry_price, signal.confidence,
            )
            return "signal"
        else:
            logger.error(
                "Order execution failed for %s: %s", ticker, order_result.error
            )
            return None

    def _check_exit(self, position: dict) -> None:
        """Check if an existing position should be exited based on sentiment."""
        ticker = position["ticker"]

        start = datetime.utcnow() - timedelta(hours=24)
        articles = self.news.fetch_news(ticker, limit=10, start=start)

        if not articles:
            return

        records = self.sentiment.score_articles(articles)
        avg_sentiment = self.sentiment.aggregate_sentiment(records)

        sell_threshold = CONFIG.get("trading", {}).get("sentiment_sell_threshold", -0.4)

        if avg_sentiment < sell_threshold:
            logger.info(
                "Exit signal for %s: sentiment %.2f < threshold %.2f",
                ticker, avg_sentiment, sell_threshold,
            )
            result = self.broker.close_position(ticker)

            if result.success:
                # Find and close the trade in DB
                open_trades = self.db.get_trades_by_ticker(ticker)
                for t in open_trades:
                    if t["status"] == "open":
                        exit_price = position["current_price"]
                        entry_price = float(t["entry_price"])
                        qty = int(t["quantity"])
                        pnl = (exit_price - entry_price) * qty

                        self.db.close_trade(
                            trade_id=t["id"],
                            exit_price=exit_price,
                            status=TradeStatus.CLOSED,
                            pnl=round(pnl, 2),
                            closed_at=datetime.utcnow().isoformat(),
                        )

    def classify_watchlist_themes(self) -> None:
        """Runs Claude-powered theme classification on the watchlist."""
        logger.info("Classifying watchlist against themes...")
        self.themes.classify_stocks(self.watchlist.symbols)
        logger.info("Theme classification complete.")

    def get_status(self) -> dict:
        """Returns current bot status for display."""
        try:
            account = self.market.get_account()
            positions = self.market.get_positions()
            stats = self.db.get_trade_stats()
            open_trades = self.db.get_open_trades()

            return {
                "account": account,
                "positions": positions,
                "open_trades": len(open_trades),
                "stats": stats,
                "peak_value": self._peak_value,
                "themes": [t.name for t in self.themes.active_themes],
            }
        except Exception as e:
            return {"error": str(e)}
