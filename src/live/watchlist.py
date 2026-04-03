"""Dynamic watchlist with aging for live trading.

Tickers are added by Call 1 (discovery) and removed when a position is opened
or after 30 days with no action. Max 50 tickers — oldest evicted at cap.
Persisted to JSON so state survives restarts.

The watchlist serves two purposes:
1. Additional context for Call 3 — "Call 1 flagged these as interesting"
2. Trigger check scope — holdings + watchlist are monitored for volatility shocks

Call 3 still screens the full universe (like the sim), but the watchlist
highlights what Call 1 thinks is hot right now.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_WATCHLIST = 20
MAX_AGE_DAYS = 30


class LiveWatchlist:
    def __init__(self, path: str | Path = "data/live/watchlist.json"):
        self._path = Path(path)
        self._entries: list[dict] = []
        self._load()

    def add(self, ticker: str, source: str = "call1", reason: str = "") -> bool:
        """Add a ticker. Returns True if added, False if already present."""
        ticker = ticker.upper().strip()
        if not ticker:
            return False
        if self.contains(ticker):
            return False

        # Evict oldest if at cap
        if len(self._entries) >= MAX_WATCHLIST:
            evicted = self._entries.pop(0)
            logger.info(
                "Watchlist at cap (%d), evicted %s (added %s)",
                MAX_WATCHLIST, evicted["ticker"], evicted["added_date"],
            )

        self._entries.append({
            "ticker": ticker,
            "added_date": date.today().isoformat(),
            "source": source,
            "reason": reason,
        })
        self._save()
        logger.info("Watchlist: added %s (source=%s, reason=%s)", ticker, source, reason)
        return True

    def remove(self, ticker: str) -> None:
        """Remove a ticker from the watchlist."""
        ticker = ticker.upper().strip()
        self._entries = [e for e in self._entries if e["ticker"] != ticker]
        self._save()

    def prune(self) -> list[str]:
        """Remove tickers older than MAX_AGE_DAYS. Returns list of pruned tickers."""
        cutoff = (date.today() - timedelta(days=MAX_AGE_DAYS)).isoformat()
        pruned = [e["ticker"] for e in self._entries if e["added_date"] < cutoff]
        if pruned:
            self._entries = [e for e in self._entries if e["added_date"] >= cutoff]
            self._save()
            logger.info("Watchlist: pruned %d stale tickers: %s", len(pruned), pruned)
        return pruned

    def get_tickers(self) -> list[str]:
        """Return list of all watchlisted tickers."""
        return [e["ticker"] for e in self._entries]

    def contains(self, ticker: str) -> bool:
        ticker = ticker.upper().strip()
        return any(e["ticker"] == ticker for e in self._entries)

    def get_entries(self) -> list[dict]:
        """Return full entry dicts (ticker, added_date, source, reason)."""
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._entries = json.loads(self._path.read_text())
            except (json.JSONDecodeError, ValueError):
                logger.warning("Corrupt watchlist file, starting fresh")
                self._entries = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._entries, indent=2))
