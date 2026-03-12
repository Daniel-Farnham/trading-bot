"""Run a historical simulation of the trading bot.

Usage:
    python -m src.simulation.run_sim --start 2024-01-01 --end 2025-01-01 --cash 100000

Requires ALPACA_API_KEY and ALPACA_SECRET_KEY in .env for historical data.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.simulation.engine import SimulationEngine


def main():
    parser = argparse.ArgumentParser(description="Run trading bot simulation")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--cash", type=float, default=100000.0, help="Initial cash")
    parser.add_argument(
        "--tickers", nargs="+", default=None,
        help="Ticker symbols to trade (default: config watchlist)"
    )
    parser.add_argument("--no-news", action="store_true", help="Skip real news, use price-derived sentiment only")
    parser.add_argument("--adapt", action="store_true", help="Enable Claude-powered adaptation layer (calls claude -p every review-interval days)")
    parser.add_argument("--review-interval", type=int, default=5, help="Days between adaptation reviews (only used with --adapt)")
    parser.add_argument("--output", default=None, help="Save report to JSON file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Suppress noisy HTTP library loggers even in verbose mode
    for noisy in ("httpcore", "httpx", "urllib3", "alpaca", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    engine = SimulationEngine(
        start_date=args.start,
        end_date=args.end,
        initial_cash=args.cash,
        watchlist=args.tickers,
        use_real_news=not args.no_news,
        enable_adaptation=args.adapt,
        review_interval_days=args.review_interval,
    )

    report = engine.run()

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nReport saved to {output_path}")

    return report


if __name__ == "__main__":
    main()
