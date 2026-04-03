"""Dynamic universe for live trading.

Seeded from the config universe on first boot. Call 1 can add new tickers
when it discovers opportunities outside the current universe. Call 3 operates
within this universe for technicals/fundamentals screening.

Tickers are never automatically removed — only pruned during monthly review
or manually.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from src.config import CONFIG

logger = logging.getLogger(__name__)


class LiveUniverse:
    def __init__(self, path: str | Path = "data/live/universe.json"):
        self._path = Path(path)
        self._entries: list[dict] = []
        self._load()

    def add(self, ticker: str, source: str = "call1", reason: str = "") -> bool:
        """Add a ticker to the universe. Returns True if added, False if already present."""
        ticker = ticker.upper().strip()
        if not ticker:
            return False
        if self.contains(ticker):
            return False

        self._entries.append({
            "ticker": ticker,
            "added_date": date.today().isoformat(),
            "source": source,
            "reason": reason,
        })
        self._save()
        logger.info("Universe: added %s (source=%s, reason=%s)", ticker, source, reason)
        return True

    def remove(self, ticker: str) -> None:
        """Remove a ticker from the universe."""
        ticker = ticker.upper().strip()
        self._entries = [e for e in self._entries if e["ticker"] != ticker]
        self._save()

    def get_tickers(self) -> list[str]:
        """Return list of all universe tickers."""
        return [e["ticker"] for e in self._entries]

    def contains(self, ticker: str) -> bool:
        ticker = ticker.upper().strip()
        return any(e["ticker"] == ticker for e in self._entries)

    def get_entries(self) -> list[dict]:
        """Return full entry dicts (ticker, added_date, source, reason)."""
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def seed_from_config(self) -> int:
        """Seed universe from config. Only adds tickers not already present.

        Returns number of tickers added.
        """
        universe_config = CONFIG.get("universe", {})
        added = 0
        seen = set()
        for theme_tickers in universe_config.values():
            for ticker in theme_tickers:
                ticker = ticker.upper().strip()
                if ticker and ticker not in seen:
                    seen.add(ticker)
                    if self.add(ticker, source="config", reason="seeded from config"):
                        added += 1
        if added:
            logger.info("Universe: seeded %d tickers from config", added)
        return added

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._entries = json.loads(self._path.read_text())
            except (json.JSONDecodeError, ValueError):
                logger.warning("Corrupt universe file, starting fresh")
                self._entries = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._entries, indent=2))
