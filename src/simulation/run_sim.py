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
from src.simulation.report import generate_report, save_equity_curve


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
    parser.add_argument("--review-interval", type=int, default=7, help="Days between daily tactical reviews (only used with --adapt)")
    parser.add_argument("--weekly-interval", type=int, default=30, help="Days between weekly strategic reviews (only used with --adapt)")
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
        weekly_interval_days=args.weekly_interval,
    )

    report = engine.run()

    # Generate human-readable report
    text_report = generate_report(report, engine.daily_snapshots, adapt=args.adapt)
    print(text_report)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save JSON report
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"JSON report saved to {output_path}")

        # Save text report
        txt_path = output_path.with_suffix(".txt")
        with open(txt_path, "w") as f:
            f.write(text_report)
        print(f"Text report saved to {txt_path}")

        # Save equity curve CSV
        csv_path = output_path.with_name(output_path.stem + "_equity_curve.csv")
        save_equity_curve(engine.daily_snapshots, csv_path)
        print(f"Equity curve saved to {csv_path}")

    return report


if __name__ == "__main__":
    main()
