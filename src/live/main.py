"""Entry point for live trading bot.

Initializes all components, reconciles state, and starts the scheduler.
Run with: python -m src.live.main
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from src.config import CONFIG, get_alpaca_keys, get_anthropic_key, get_gmail_credentials
from src.analysis.technical import TechnicalAnalyzer
from src.data.market import MarketData
from src.data.options_data import OptionsDataClient
from src.execution.broker import Broker
from src.execution.options_broker import OptionsBroker
from src.live.claude_client import ClaudeClient
from src.live.executor import LiveExecutor
from src.live.health import start_health_server, set_data_dir, set_market_data, set_orchestrator
from src.live.notifier import EmailNotifier
from src.live.orchestrator import LiveOrchestrator
from src.live.pending_orders import PendingOrderTracker
from src.live.scheduler import create_scheduler
from src.live.trigger_check import TriggerCheck
from src.live.universe import LiveUniverse
from src.live.watchlist import LiveWatchlist
from src.research.fundamentals import FundamentalsClient
from src.research.news_client import AlpacaNewsClient
from src.strategy.contract_selector import ContractSelector
from src.strategy.decision_engine import DecisionEngine
from src.strategy.risk_v3 import RiskManagerV3
from src.strategy.thesis_manager import ThesisManager

logger = logging.getLogger(__name__)


def _force_first_boot_reset(broker, market_data, data_dir: str) -> None:
    """Full reset: liquidate Alpaca → wipe local files → set today as inception.

    Sequence is intentional. We liquidate FIRST so the equity figure we
    write into the new inception.json reflects the post-reset cash balance
    (positions become cash, modulo slippage — equity is conserved). The
    file wipe happens AFTER liquidation but BEFORE the inception write so
    the new inception.json doesn't get caught in the wipe.

    Refuses to liquidate on a non-paper broker (defense in depth — paper
    is hardcoded today, but a future change to enable live trading must
    not silently break this guard).
    """
    from datetime import date
    import json
    import time

    logger.warning("=" * 60)
    logger.warning("FORCE_FIRST_BOOT=true — full reset in progress")
    logger.warning("=" * 60)

    # 1. Liquidate Alpaca (paper-only; refuses on live)
    if broker.is_paper:
        logger.info("Step 1/3: liquidating Alpaca paper account")
        ok = broker.close_all_positions(cancel_orders=True)
        if not ok:
            logger.error(
                "Liquidation request failed — continuing with reset anyway. "
                "Check Alpaca dashboard for stuck positions."
            )
        # Brief pause to let market sells fill (no-op when market closed —
        # orders queue as GTC and fill at next open; reconciler catches up).
        time.sleep(2)
    else:
        logger.error(
            "Broker reports non-paper account — SKIPPING liquidation. "
            "Files will still be wiped, but Alpaca positions remain. "
            "If this is wrong, check Broker.__init__(paper=...)."
        )

    # 2. Wipe local files
    logger.info("Step 2/3: wiping local state files")
    wiped = 0
    for f in Path(data_dir).glob("*"):
        if f.is_file():
            f.unlink()
            wiped += 1
    logger.info("  Wiped %d files from %s", wiped, data_dir)

    # 3. Write fresh inception.json with current equity as baseline.
    # Both Total Return and SPY Return rebase to this date (see
    # src/live/portfolio_state.py:_spy_return_since — same start date).
    logger.info("Step 3/3: writing fresh inception.json")
    try:
        account = market_data.get_account()
        equity = float(account.get("equity", account.get("portfolio_value", 0)))
    except Exception as e:
        logger.error("Could not fetch post-reset equity: %s — defaulting to 100000", e)
        equity = 100000.0
    inception = {"start_date": date.today().isoformat(), "initial_value": round(equity, 2)}
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / "inception.json").write_text(json.dumps(inception, indent=2))
    logger.info(
        "  inception.json: start_date=%s, initial_value=$%.2f",
        inception["start_date"], inception["initial_value"],
    )
    logger.warning("FORCE_FIRST_BOOT reset complete — bot will rebuild from clean state")


def main() -> None:
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info("=== Live Trading Bot Starting ===")

    # Load config
    live_cfg = CONFIG.get("live", {})
    anthropic_cfg = CONFIG.get("anthropic", {})
    email_cfg = CONFIG.get("email", {})
    data_dir = live_cfg.get("data_dir", "data/live")

    # Initialize API clients
    api_key, secret_key = get_alpaca_keys()
    anthropic_key = get_anthropic_key()

    # Claude client with hard spend caps
    claude_client = ClaudeClient(
        api_key=anthropic_key,
        daily_budget_usd=anthropic_cfg.get("daily_budget_usd", 2.00),
        monthly_budget_usd=anthropic_cfg.get("monthly_budget_usd", 40.00),
        spend_log_path=os.path.join(data_dir, "api_spend.jsonl"),
    )

    # Market data + broker
    market_data = MarketData(api_key=api_key, secret_key=secret_key)
    broker = Broker(api_key=api_key, secret_key=secret_key)

    # Thesis manager — override config memory paths to use live data directory
    # ThesisManager reads from CONFIG["memory"] and prepends base_dir
    # We override the config paths to point to the live dir's filenames
    memory_cfg = CONFIG.setdefault("memory", {})
    for key in ["theses_path", "ledger_path", "summaries_path", "lessons_path",
                 "themes_path", "beliefs_path", "world_view_path", "decision_journal_path"]:
        default = memory_cfg.get(key, "")
        filename = os.path.basename(default) if default else f"{key.replace('_path', '')}.md"
        memory_cfg[key] = os.path.join(data_dir, filename)

    thesis_manager = ThesisManager()

    # Strategy components
    risk_manager = RiskManagerV3()
    technical_analyzer = TechnicalAnalyzer()
    fundamentals_client = FundamentalsClient()

    # Decision engine with SDK client
    decision_engine = DecisionEngine(
        thesis_manager=thesis_manager,
        model=anthropic_cfg.get("call3_model", "sonnet"),
        claude_client=claude_client,
    )

    # News client
    news_client = AlpacaNewsClient()

    # Options components
    options_data = OptionsDataClient(api_key=api_key, secret_key=secret_key)
    options_broker = OptionsBroker(api_key=api_key, secret_key=secret_key)
    contract_selector = ContractSelector(options_data=options_data)

    # Live components
    watchlist = LiveWatchlist(path=os.path.join(data_dir, "watchlist.json"))
    universe = LiveUniverse(path=os.path.join(data_dir, "universe.json"))
    trigger_check = TriggerCheck(
        market_data=market_data,
        technical_analyzer=technical_analyzer,
    )
    executor = LiveExecutor(
        broker=broker,
        risk_manager=risk_manager,
        thesis_manager=thesis_manager,
        market_data=market_data,
        options_broker=options_broker,
        contract_selector=contract_selector,
    )

    # Email notifications
    email_enabled = email_cfg.get("enabled", True)
    if email_enabled:
        try:
            gmail_address, gmail_password = get_gmail_credentials()
            notifier = EmailNotifier(
                sender=gmail_address,
                app_password=gmail_password,
                recipient=gmail_address,  # Send to self
                enabled=True,
            )
        except EnvironmentError:
            logger.warning("Gmail credentials not set — email notifications disabled")
            notifier = EmailNotifier(sender="", app_password="", recipient="", enabled=False)
    else:
        notifier = EmailNotifier(sender="", app_password="", recipient="", enabled=False)

    # Pending order tracker
    pending_tracker = PendingOrderTracker(
        path=os.path.join(data_dir, "pending_orders.json"),
    )

    # Orchestrator — wires everything together
    orchestrator = LiveOrchestrator(
        claude_client=claude_client,
        decision_engine=decision_engine,
        thesis_manager=thesis_manager,
        market_data=market_data,
        technical_analyzer=technical_analyzer,
        fundamentals_client=fundamentals_client,
        news_client=news_client,
        trigger_check=trigger_check,
        executor=executor,
        watchlist=watchlist,
        universe=universe,
        notifier=notifier,
        pending_tracker=pending_tracker,
        state_path=os.path.join(data_dir, "daily_state.json"),
    )

    # Start dashboard server FIRST so Railway health check passes during startup
    set_data_dir(data_dir)
    set_market_data(market_data)
    set_orchestrator(orchestrator)
    health_port = int(os.environ.get("PORT", 8080))
    start_health_server(port=health_port)

    # Force fresh start if requested (set FORCE_FIRST_BOOT=true on Railway, then remove after)
    force_first_boot = os.environ.get("FORCE_FIRST_BOOT", "").lower() == "true"
    if force_first_boot:
        _force_first_boot_reset(broker, market_data, data_dir)
        # Recreate components with clean state (files wiped above)
        watchlist = LiveWatchlist(path=os.path.join(data_dir, "watchlist.json"))
        universe = LiveUniverse(path=os.path.join(data_dir, "universe.json"))
        orchestrator._watchlist = watchlist
        orchestrator._universe = universe

    # Startup sequence
    orchestrator.reconcile_on_startup()

    # First boot: seed universe + build world view + themes + watchlist
    if len(universe) == 0 or force_first_boot:
        orchestrator.initialize_first_boot()

    # Start scheduler (blocking)
    logger.info("Starting scheduler — bot is live.")
    scheduler = create_scheduler(orchestrator)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
