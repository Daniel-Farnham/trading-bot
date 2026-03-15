"""Run a V3 thesis-driven simulation.

Usage:
    python -m src.simulation.run_thesis_sim --start 2024-01-01 --end 2024-06-30 --cash 100000

Requires:
- ALPACA_API_KEY and ALPACA_SECRET_KEY in .env (historical price data)
- Claude Code CLI installed (thesis reviews)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.simulation.report import save_equity_curve
from src.simulation.thesis_sim import ThesisSimulation
from src.strategy.belief_consolidator import classify_regime, consolidate_beliefs


def main():
    parser = argparse.ArgumentParser(description="Run V3 thesis-driven simulation")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--cash", type=float, default=100_000.0, help="Initial cash")
    parser.add_argument("--review-cadence", type=int, default=5, help="Days between reviews (default: 5 = weekly)")
    parser.add_argument("--data-dir", default=None, help="Directory for sim memory files (default: data/v3_sim)")
    parser.add_argument("--output", default=None, help="Save report to JSON file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--notes", default="", help="Notes for this run (saved in JSON report)")
    parser.add_argument(
        "--seed-theme", action="append", default=[], metavar="NAME:DESC",
        help="Seed a macro theme (score 1). Format: 'Name:Description'. Can be repeated.",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_format = "%(message)s" if not args.verbose else "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Suppress noisy loggers
    for noisy in ("httpcore", "httpx", "urllib3", "alpaca", "websockets", "yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("src.simulation.sim_broker").setLevel(logging.WARNING)

    # Parse seed themes from CLI
    seed_themes = []
    for theme_str in args.seed_theme:
        if ":" in theme_str:
            name, desc = theme_str.split(":", 1)
            seed_themes.append((name.strip(), desc.strip()))

    sim = ThesisSimulation(
        start_date=args.start,
        end_date=args.end,
        initial_cash=args.cash,
        review_cadence_days=args.review_cadence,
        data_dir=args.data_dir,
        seed_themes=seed_themes or None,
    )

    report = sim.run()

    # Consolidate beliefs from this run into seed beliefs
    lessons = sim.thesis_manager.get_all_lessons()
    beliefs = sim.thesis_manager.get_all_beliefs()
    regime = classify_regime(report)
    logger = logging.getLogger(__name__)
    logger.info("Consolidating beliefs (regime: %s)...", regime)
    consolidate_beliefs(lessons, beliefs, regime, report)

    # Save outputs
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nJSON report saved to {output_path}")

        # Text summary report
        txt_path = output_path.with_suffix(".txt")
        txt_report = _generate_text_report(report, sim)
        with open(txt_path, "w") as f:
            f.write(txt_report)
        print(f"Text report saved to {txt_path}")

        csv_path = output_path.with_name(output_path.stem + "_equity_curve.csv")
        save_equity_curve(sim.daily_snapshots, csv_path)
        print(f"Equity curve saved to {csv_path}")

    return report


def _generate_text_report(report: dict, sim: ThesisSimulation) -> str:
    """Generate a human-readable text summary of the simulation."""
    lines = []
    lines.append("=" * 60)
    lines.append("  V3 THESIS SIMULATION REPORT")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Period:              {report['period']}")
    lines.append(f"  Trading Days:        {report['trading_days']}")
    lines.append(f"  Weekly Reviews:      {report['weekly_reviews']}")
    lines.append("")
    lines.append("  PERFORMANCE")
    lines.append(f"  Initial Capital:     ${report['initial_cash']:,.2f}")
    lines.append(f"  Final Value:         ${report['final_value']:,.2f}")
    lines.append(f"  Total Return:        {report['total_return_pct']:+.2f}%")
    lines.append(f"  Annualized Return:   {report['annualized_return_pct']:+.2f}%")
    lines.append(f"  Max Drawdown:        -{report['max_drawdown_pct']:.2f}%")
    lines.append("")
    lines.append("  TRADES")
    lines.append(f"  Total Trades:        {report['total_trades']} ({report['wins']}W / {report['losses']}L)")
    lines.append(f"  Win Rate:            {report['win_rate_pct']:.1f}%")
    lines.append(f"  Total P&L:           ${report['total_pnl']:+,.2f}")
    lines.append(f"  Avg P&L/Trade:       ${report['avg_pnl_per_trade']:+,.2f}")
    lines.append(f"  Open Positions:      {report['open_positions']}")
    lines.append("")

    # Per-ticker breakdown from closed trades
    closed = report.get("closed_trades", [])
    if closed:
        from collections import defaultdict
        ticker_pnl: dict[str, float] = defaultdict(float)
        ticker_count: dict[str, int] = defaultdict(int)
        for t in closed:
            ticker_pnl[t["ticker"]] += t.get("pnl", 0)
            ticker_count[t["ticker"]] += 1
        lines.append("  PER-TICKER PERFORMANCE")
        for tk in sorted(ticker_pnl, key=lambda x: -ticker_pnl[x]):
            lines.append(f"    {tk:8s}  ${ticker_pnl[tk]:+10,.2f}  ({ticker_count[tk]} trades)")
        lines.append("")

    # Lessons learned
    lessons = sim.thesis_manager.get_all_lessons()
    if lessons:
        lines.append(f"  LESSONS LEARNED ({len(lessons)} total)")
        for lesson in lessons:
            content = lesson["content"] if isinstance(lesson, dict) else str(lesson)
            lines.append(f"    - [score {lesson.get('score', '?')}/5] {content}")
        lines.append("")

    # Active theses at end
    theses = sim.thesis_manager.get_all_theses()
    if theses:
        active = [t for t in theses if t.get("status", "").upper() == "ACTIVE"]
        if active:
            lines.append(f"  ACTIVE THESES ({len(active)})")
            for t in active:
                thesis_text = t.get("thesis", "")[:80]
                lines.append(f"    {t['ticker']} ({t['direction']}): {thesis_text}")
            lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    main()
