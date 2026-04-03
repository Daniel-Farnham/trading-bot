"""APScheduler configuration for live trading.

Schedule (all US/Eastern):
- Call 1:         09:00 AM Mon-Fri
- Trigger check:  Every 30 min, 09:30 AM - 3:00 PM Mon-Fri
- Call 3:         03:30 PM every Friday (weekly review)
- Call 3:         Immediately on trigger (any weekday)
- EOD portfolio:  04:30 PM Mon-Fri (no Claude)

Monthly review on 3rd Friday (Call 3 with review_type="monthly").
Skips US market holidays via exchange-calendars.
"""
from __future__ import annotations

import logging
from datetime import date

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import CONFIG
from src.live.orchestrator import LiveOrchestrator

logger = logging.getLogger(__name__)


def _is_third_friday(d: date) -> bool:
    """Check if a date is the 3rd Friday of its month."""
    if d.weekday() != 4:  # Friday
        return False
    # 3rd Friday is between the 15th and 21st
    return 15 <= d.day <= 21


def _is_market_holiday(d: date) -> bool:
    """Check if a date is a US market holiday."""
    try:
        import exchange_calendars as xcals
        nyse = xcals.get_calendar("XNYS")
        return not nyse.is_session(d.isoformat())
    except Exception:
        # If exchange-calendars not available, assume it's a trading day
        return False


def create_scheduler(orchestrator: LiveOrchestrator) -> BlockingScheduler:
    """Create and configure the APScheduler with all jobs."""
    scheduler_cfg = CONFIG.get("scheduler", {})
    timezone = scheduler_cfg.get("timezone", "US/Eastern")

    scheduler = BlockingScheduler(timezone=timezone)

    # Call 1: Daily at 09:00 AM Mon-Fri
    call1_time = scheduler_cfg.get("call1_time", "09:00")
    call1_hour, call1_min = call1_time.split(":")
    scheduler.add_job(
        _run_call1,
        CronTrigger(
            day_of_week="mon-fri",
            hour=int(call1_hour),
            minute=int(call1_min),
            timezone=timezone,
        ),
        args=[orchestrator],
        id="call1",
        name="Call 1: Discovery & Screening",
        misfire_grace_time=300,
    )

    # Trigger check: Every 30 min, 09:30 AM - 3:00 PM Mon-Fri
    interval = scheduler_cfg.get("trigger_check_interval_minutes", 30)
    trigger_start = scheduler_cfg.get("trigger_check_start", "09:30")
    trigger_end = scheduler_cfg.get("trigger_check_end", "15:00")
    start_hour, start_min = trigger_start.split(":")
    end_hour, _ = trigger_end.split(":")

    scheduler.add_job(
        _run_trigger_check,
        CronTrigger(
            day_of_week="mon-fri",
            hour=f"{start_hour}-{end_hour}",
            minute=f"{start_min}/{interval}",
            timezone=timezone,
        ),
        args=[orchestrator],
        id="trigger_check",
        name="Trigger Check: Volatility Monitor",
        misfire_grace_time=120,
    )

    # Call 3: Friday at 3:30 PM (weekly review)
    call3_time = scheduler_cfg.get("call3_friday_time", "15:30")
    call3_hour, call3_min = call3_time.split(":")
    scheduler.add_job(
        _run_friday_call3,
        CronTrigger(
            day_of_week="fri",
            hour=int(call3_hour),
            minute=int(call3_min),
            timezone=timezone,
        ),
        args=[orchestrator],
        id="friday_call3",
        name="Call 3: Friday Weekly Review",
        misfire_grace_time=300,
    )

    # EOD portfolio: Daily at 4:30 PM Mon-Fri
    eod_time = scheduler_cfg.get("eod_portfolio_time", "16:30")
    eod_hour, eod_min = eod_time.split(":")
    scheduler.add_job(
        _run_eod_portfolio,
        CronTrigger(
            day_of_week="mon-fri",
            hour=int(eod_hour),
            minute=int(eod_min),
            timezone=timezone,
        ),
        args=[orchestrator],
        id="eod_portfolio",
        name="EOD Portfolio Email",
        misfire_grace_time=300,
    )

    logger.info("Scheduler configured:")
    logger.info("  Call 1:         %s Mon-Fri", call1_time)
    logger.info("  Trigger check:  Every %d min, %s-%s Mon-Fri", interval, trigger_start, trigger_end)
    logger.info("  Call 3 (Fri):   %s Friday", call3_time)
    logger.info("  EOD Portfolio:  %s Mon-Fri", eod_time)
    logger.info("  Timezone:       %s", timezone)

    return scheduler


def _run_call1(orchestrator: LiveOrchestrator) -> None:
    today = date.today()
    if _is_market_holiday(today):
        logger.info("Market holiday — skipping Call 1")
        return
    orchestrator.run_call1()


def _run_trigger_check(orchestrator: LiveOrchestrator) -> None:
    today = date.today()
    if _is_market_holiday(today):
        return
    orchestrator.run_trigger_check()


def _run_friday_call3(orchestrator: LiveOrchestrator) -> None:
    today = date.today()
    if _is_market_holiday(today):
        logger.info("Market holiday — skipping Friday Call 3")
        return

    review_type = "monthly" if _is_third_friday(today) else "weekly"
    orchestrator.run_call3(review_type=review_type)


def _run_eod_portfolio(orchestrator: LiveOrchestrator) -> None:
    today = date.today()
    if _is_market_holiday(today):
        return
    orchestrator.run_eod_portfolio()
