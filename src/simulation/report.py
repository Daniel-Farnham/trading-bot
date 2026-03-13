"""Human-readable simulation report generator.

Produces a formatted text report and equity curve CSV from simulation results.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path


def generate_report(report: dict, daily_snapshots: list[dict], adapt: bool = False) -> str:
    """Generate a formatted text report from simulation results."""
    lines = []
    w = lines.append  # shorthand

    mode = "WITH Adaptation" if adapt else "DRY RUN (No Adaptation)"

    w("")
    w("=" * 65)
    w(f"  SIMULATION REPORT — {mode}")
    w("=" * 65)

    # --- Performance Summary ---
    w("")
    w("  PERFORMANCE SUMMARY")
    w("  " + "-" * 40)
    w(f"  Period:              {report['period']}")
    w(f"  Trading Days:        {report['trading_days']}")
    w(f"  Initial Capital:     ${report['initial_cash']:,.2f}")
    w(f"  Final Value:         ${report['final_value']:,.2f}")

    ret = report["total_return_pct"]
    ret_sign = "+" if ret >= 0 else ""
    w(f"  Total Return:        {ret_sign}{ret:.2f}%")

    ann = report["annualized_return_pct"]
    ann_sign = "+" if ann >= 0 else ""
    w(f"  Annualized Return:   {ann_sign}{ann:.2f}%")
    w(f"  Max Drawdown:        -{report['max_drawdown_pct']:.2f}%")

    # --- Trade Statistics ---
    w("")
    w("  TRADE STATISTICS")
    w("  " + "-" * 40)
    total = report["total_trades"]
    wins = report["wins"]
    losses = report["losses"]
    w(f"  Total Trades:        {total}")
    w(f"  Win Rate:            {report['win_rate_pct']:.1f}% ({wins}W / {losses}L)")

    closed = report.get("closed_trades", [])

    # Avg win / avg loss
    winning = [t for t in closed if t.get("pnl", 0) > 0]
    losing = [t for t in closed if t.get("pnl", 0) <= 0]
    avg_win = sum(t["pnl"] for t in winning) / len(winning) if winning else 0
    avg_loss = sum(t["pnl"] for t in losing) / len(losing) if losing else 0
    w(f"  Avg Win:             +${avg_win:,.2f}")
    w(f"  Avg Loss:            -${abs(avg_loss):,.2f}")

    pnl = report["total_pnl"]
    pnl_sign = "+" if pnl >= 0 else ""
    w(f"  Total P&L:           {pnl_sign}${pnl:,.2f}")
    w(f"  Avg P&L/Trade:       ${report['avg_pnl_per_trade']:,.2f}")

    # Best / worst trade
    if closed:
        best = max(closed, key=lambda t: t.get("pnl", 0))
        worst = min(closed, key=lambda t: t.get("pnl", 0))
        w(f"  Best Trade:          {best['ticker']} +${best['pnl']:,.2f}")
        w(f"  Worst Trade:         {worst['ticker']} -${abs(worst['pnl']):,.2f}")

    # Exit reasons breakdown
    reasons = defaultdict(int)
    for t in closed:
        reasons[t.get("exit_reason", "closed")] += 1
    if reasons:
        w("")
        w("  EXIT REASONS")
        w("  " + "-" * 40)
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            label = reason.replace("_", " ").title()
            w(f"  {label:<22} {count:>4} ({count/len(closed)*100:.0f}%)")

    # --- Per-Ticker Breakdown ---
    if closed:
        w("")
        w("  PER-TICKER BREAKDOWN")
        w("  " + "-" * 55)
        w(f"  {'Ticker':<8} {'Trades':>6} {'Wins':>5} {'Win%':>6} {'Total P&L':>12} {'Avg P&L':>10}")
        w("  " + "-" * 55)

        ticker_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        for t in closed:
            tk = t["ticker"]
            ticker_stats[tk]["trades"] += 1
            ticker_stats[tk]["pnl"] += t.get("pnl", 0)
            if t.get("pnl", 0) > 0:
                ticker_stats[tk]["wins"] += 1

        # Sort by total P&L descending
        for tk, s in sorted(ticker_stats.items(), key=lambda x: -x[1]["pnl"]):
            wr = s["wins"] / s["trades"] * 100 if s["trades"] > 0 else 0
            avg = s["pnl"] / s["trades"]
            sign = "+" if s["pnl"] >= 0 else ""
            w(f"  {tk:<8} {s['trades']:>6} {s['wins']:>5} {wr:>5.0f}% {sign}${s['pnl']:>10,.2f} ${avg:>9,.2f}")

    # --- Full Trade Log ---
    if closed:
        w("")
        w("  FULL TRADE LOG")
        w("  " + "-" * 70)
        w(f"  {'#':<4} {'Ticker':<7} {'Entry':>9} {'Exit':>9} {'Qty':>5} {'P&L':>10} {'Reason'}")
        w("  " + "-" * 70)

        for i, t in enumerate(closed, 1):
            pnl_val = t.get("pnl", 0)
            sign = "+" if pnl_val >= 0 else ""
            reason = t.get("exit_reason", "closed").replace("_", " ")
            w(
                f"  {i:<4} {t['ticker']:<7} "
                f"${t['entry_price']:>8,.2f} "
                f"${t['exit_price']:>8,.2f} "
                f"{t['quantity']:>5} "
                f"{sign}${pnl_val:>9,.2f} "
                f"{reason}"
            )

    # --- Adaptation Summary ---
    if report.get("adaptation_reviews", 0) > 0:
        w("")
        w("  ADAPTATION REVIEWS")
        w("  " + "-" * 40)
        w(f"  Total Reviews:       {report['adaptation_reviews']}")
        for a in report.get("adaptations", []):
            changes = a.get("result", {}).get("changes", [])
            w(f"  {a['date']}: {len(changes)} parameter change(s)")
            for c in changes:
                w(f"    {c['param']}: {c.get('old_value', '?')} → {c.get('new_value', '?')}")

    # --- Open Positions ---
    if report.get("open_positions", 0) > 0:
        w("")
        w(f"  OPEN POSITIONS AT END: {report['open_positions']}")

    w("")
    w("=" * 65)
    w("")

    return "\n".join(lines)


def save_equity_curve(daily_snapshots: list[dict], output_path: Path) -> None:
    """Save daily equity curve as CSV for charting."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "portfolio_value", "cash", "positions", "total_pnl"])
        for snap in daily_snapshots:
            writer.writerow([
                snap["date"],
                snap["portfolio_value"],
                snap["cash"],
                snap["positions"],
                snap["total_pnl"],
            ])
