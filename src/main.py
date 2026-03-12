from __future__ import annotations

import logging
import signal
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.bot import TradingBot
from src.config import CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/trading.log"),
    ],
)
logger = logging.getLogger(__name__)


def create_bot() -> TradingBot:
    bot = TradingBot()
    logger.info("Trading bot initialized.")
    logger.info("Watchlist: %s", ", ".join(bot.watchlist.symbols))
    logger.info("Active themes: %s", ", ".join(t.name for t in bot.themes.active_themes))
    return bot


def run_trading_cycle(bot: TradingBot) -> None:
    logger.info("--- Starting trading cycle ---")
    try:
        summary = bot.run_cycle()
        logger.info("Cycle summary: %s", summary)
    except Exception as e:
        logger.error("Trading cycle failed: %s", e, exc_info=True)


def run_daily_review(bot: TradingBot) -> None:
    logger.info("--- Starting daily review ---")
    try:
        status = bot.get_status()
        stats = status.get("stats", {})
        logger.info(
            "Daily stats — Total trades: %d, Win rate: %.1f%%, Total P&L: $%.2f",
            stats.get("total", 0),
            stats.get("win_rate", 0) * 100,
            stats.get("total_pnl", 0),
        )
    except Exception as e:
        logger.error("Daily review failed: %s", e, exc_info=True)


def run_theme_classification(bot: TradingBot) -> None:
    logger.info("--- Running theme classification ---")
    try:
        bot.classify_watchlist_themes()
    except Exception as e:
        logger.error("Theme classification failed: %s", e, exc_info=True)


def main() -> None:
    bot = create_bot()

    # Classify watchlist themes on startup
    run_theme_classification(bot)

    scheduler = BlockingScheduler()

    # Trading loop: every 30 minutes during market hours (Mon-Fri, 9:30-16:00 ET)
    interval = CONFIG.get("scheduler", {}).get("loop_interval_minutes", 30)
    scheduler.add_job(
        run_trading_cycle,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute=f"*/{interval}",
            timezone="US/Eastern",
        ),
        args=[bot],
        id="trading_cycle",
        name="Trading Cycle",
    )

    # Also run at market open and close
    scheduler.add_job(
        run_trading_cycle,
        CronTrigger(
            day_of_week="mon-fri",
            hour=9, minute=30,
            timezone="US/Eastern",
        ),
        args=[bot],
        id="market_open_cycle",
        name="Market Open Cycle",
    )

    # Daily review at 4:30 PM ET
    scheduler.add_job(
        run_daily_review,
        CronTrigger(
            day_of_week="mon-fri",
            hour=16, minute=30,
            timezone="US/Eastern",
        ),
        args=[bot],
        id="daily_review",
        name="Daily Review",
    )

    # Weekly theme re-classification on Saturday at 10 AM ET
    scheduler.add_job(
        run_theme_classification,
        CronTrigger(
            day_of_week="sat",
            hour=10, minute=0,
            timezone="US/Eastern",
        ),
        args=[bot],
        id="theme_classification",
        name="Weekly Theme Classification",
    )

    # Graceful shutdown
    def shutdown(signum, frame):
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)
        bot.db.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("Scheduler started. Trading bot is running.")
    logger.info(
        "Schedule: every %d min (Mon-Fri 9:30-16:00 ET), "
        "daily review at 16:30 ET, "
        "theme reclassification Sat 10:00 ET",
        interval,
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
        bot.db.close()


if __name__ == "__main__":
    main()
