"""Local CLI for checking live bot status.

Usage: python -m src.live.status_cli [command]

Commands:
    status      Overview (default)
    portfolio   Alpaca account + positions
    watchlist   Current watchlist
    universe    Current universe (count + tickers)
    state       Today's daily state (Call 1/3 outputs, triggers)
    memory      Memory files (theses, themes, world view, etc.)
    spend       API spend log
    call1       Last Call 1 output (full JSON)
    call3       Last Call 3 output (full JSON)
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from src.config import CONFIG


def _data_dir() -> Path:
    return Path(CONFIG.get("live", {}).get("data_dir", "data/live"))


def _read_json(filename: str) -> dict | list | None:
    path = _data_dir() / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _read_md(filename: str) -> str:
    path = _data_dir() / filename
    if not path.exists():
        return "(not found)"
    return path.read_text()


def cmd_status():
    """Overview of bot state."""
    state = _read_json("daily_state.json") or {}
    watchlist = _read_json("watchlist.json") or []
    universe = _read_json("universe.json") or []

    print(f"\n=== Trading Bot Status ({date.today()}) ===\n")
    print(f"Daily State Date:  {state.get('date', 'N/A')}")
    print(f"Call 1:            {'Done' if state.get('call1_output') else 'Not run'}")
    print(f"Call 3:            {'Done' if state.get('call3_output') else 'Not run'}")
    print(f"Triggers Today:    {len(state.get('triggers_fired', []))}")
    print(f"Trades Today:      {len(state.get('trades_executed', []))}")
    print(f"Watchlist:         {len(watchlist)} stocks")
    print(f"Universe:          {len(universe)} stocks")

    # Spend
    spend_path = _data_dir() / "api_spend.jsonl"
    if spend_path.exists():
        today_str = date.today().isoformat()
        today_spend = 0.0
        total_spend = 0.0
        for line in spend_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                total_spend += entry.get("cost_usd", 0)
                if entry.get("date") == today_str:
                    today_spend += entry.get("cost_usd", 0)
            except Exception:
                continue
        print(f"API Spend Today:   ${today_spend:.4f}")
        print(f"API Spend Total:   ${total_spend:.2f}")

    # Triggers
    triggers = state.get("triggers_fired", [])
    if triggers:
        print(f"\nTriggers:")
        for t in triggers:
            print(f"  - {t.get('trigger_type', '?')}: {t.get('details', '')}")

    # Trades
    trades = state.get("trades_executed", [])
    if trades:
        print(f"\nTrades:")
        for t in trades:
            print(f"  - {t.get('ticker', '?')} {t.get('action', '?')}: {t.get('details', '')}")


def cmd_portfolio():
    """Show Alpaca portfolio from live API."""
    from src.data.market import MarketData
    from src.config import get_alpaca_keys

    api_key, secret_key = get_alpaca_keys()
    market = MarketData(api_key=api_key, secret_key=secret_key)

    account = market.get_account()
    positions = market.get_positions()

    print(f"\n=== Portfolio ===\n")
    print(f"Equity:       ${float(account.get('equity', account.get('portfolio_value', 0))):,.2f}")
    print(f"Cash:         ${float(account.get('cash', 0)):,.2f}")
    print(f"Buying Power: ${float(account.get('buying_power', 0)):,.2f}")

    if positions:
        print(f"\nPositions ({len(positions)}):")
        print(f"{'Ticker':<8} {'Qty':>6} {'Entry':>10} {'Current':>10} {'P&L':>10} {'P&L%':>8}")
        print("-" * 56)
        for p in positions:
            pnl = float(p.get("unrealized_pnl", 0))
            pnl_pct = float(p.get("unrealized_pnl_pct", 0)) * 100
            marker = "+" if pnl >= 0 else ""
            print(
                f"{p.get('ticker', ''):<8} "
                f"{p.get('qty', 0):>6} "
                f"${float(p.get('avg_entry', 0)):>9,.2f} "
                f"${float(p.get('current_price', 0)):>9,.2f} "
                f"{marker}${pnl:>8,.0f} "
                f"{marker}{pnl_pct:>6.1f}%"
            )
    else:
        print("\nNo open positions.")


def cmd_watchlist():
    """Show current watchlist."""
    watchlist = _read_json("watchlist.json") or []
    print(f"\n=== Watchlist ({len(watchlist)} stocks) ===\n")
    if not watchlist:
        print("(empty)")
        return
    print(f"{'Ticker':<8} {'Added':<12} {'Source':<15} {'Reason'}")
    print("-" * 70)
    for w in watchlist:
        print(f"{w.get('ticker', ''):<8} {w.get('added_date', ''):<12} {w.get('source', ''):<15} {w.get('reason', '')[:40]}")


def cmd_universe():
    """Show current universe."""
    universe = _read_json("universe.json") or []
    print(f"\n=== Universe ({len(universe)} stocks) ===\n")
    if not universe:
        print("(empty)")
        return
    # Group by source
    by_source = {}
    for u in universe:
        source = u.get("source", "unknown")
        by_source.setdefault(source, []).append(u.get("ticker", ""))
    for source, tickers in by_source.items():
        print(f"{source} ({len(tickers)}):")
        # Print in rows of 15
        for i in range(0, len(tickers), 15):
            print(f"  {', '.join(tickers[i:i+15])}")


def cmd_state():
    """Show today's daily state."""
    state = _read_json("daily_state.json")
    if not state:
        print("No daily state found.")
        return
    print(json.dumps(state, indent=2, default=str))


def cmd_memory():
    """Show memory files."""
    md_files = [
        "active_theses.md", "portfolio_ledger.md", "themes.md",
        "world_view.md", "beliefs.md", "lessons_learned.md",
    ]
    for filename in md_files:
        content = _read_md(filename)
        print(f"\n{'='*60}")
        print(f"  {filename}")
        print(f"{'='*60}")
        print(content[:2000])
        if len(content) > 2000:
            print(f"\n  ... ({len(content) - 2000} more chars)")


def cmd_spend():
    """Show API spend log."""
    spend_path = _data_dir() / "api_spend.jsonl"
    if not spend_path.exists():
        print("No spend data.")
        return

    entries = []
    for line in spend_path.read_text().splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except Exception:
                continue

    print(f"\n=== API Spend ({len(entries)} calls) ===\n")
    print(f"{'Time':<22} {'Model':<30} {'In':>8} {'Out':>8} {'Cost':>10}")
    print("-" * 82)
    for e in entries[-20:]:
        print(
            f"{e.get('timestamp', e.get('date', ''))[:21]:<22} "
            f"{e.get('model', ''):<30} "
            f"{e.get('input_tokens', 0):>8} "
            f"{e.get('output_tokens', 0):>8} "
            f"${e.get('cost_usd', 0):>9.4f}"
        )
    total = sum(e.get("cost_usd", 0) for e in entries)
    print(f"\nTotal: ${total:.4f}")


def cmd_call1():
    """Show last Call 1 output."""
    state = _read_json("daily_state.json") or {}
    output = state.get("call1_output")
    if not output:
        print("No Call 1 output today.")
        return
    print(json.dumps(output, indent=2))


def cmd_call3():
    """Show last Call 3 output."""
    state = _read_json("daily_state.json") or {}
    output = state.get("call3_output")
    if not output:
        print("No Call 3 output today.")
        return
    print(json.dumps(output, indent=2))


COMMANDS = {
    "status": cmd_status,
    "portfolio": cmd_portfolio,
    "watchlist": cmd_watchlist,
    "universe": cmd_universe,
    "state": cmd_state,
    "memory": cmd_memory,
    "spend": cmd_spend,
    "call1": cmd_call1,
    "call3": cmd_call3,
}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return
    handler = COMMANDS.get(cmd)
    if not handler:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        return
    handler()


if __name__ == "__main__":
    main()
