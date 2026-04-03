"""Inter-call state persistence for live trading.

Tracks Call 1 output, Call 3 output, triggers fired, and trades executed
for the current day. Persisted to JSON so mid-day restarts can resume.
Reset at the start of each new trading day.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DailyState:
    date: str = ""
    call1_output: dict | None = None
    call3_output: dict | None = None
    triggers_fired: list[dict] = field(default_factory=list)
    trades_executed: list[dict] = field(default_factory=list)

    def save(self, path: str | Path) -> None:
        """Write state to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, default=str))

    @classmethod
    def load(cls, path: str | Path) -> DailyState:
        """Read state from JSON file. Returns fresh state if file missing/corrupt."""
        path = Path(path)
        if not path.exists():
            return cls(date=date.today().isoformat())
        try:
            data = json.loads(path.read_text())
            return cls(
                date=data.get("date", ""),
                call1_output=data.get("call1_output"),
                call3_output=data.get("call3_output"),
                triggers_fired=data.get("triggers_fired", []),
                trades_executed=data.get("trades_executed", []),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("Corrupt daily state file, starting fresh: %s", e)
            return cls(date=date.today().isoformat())

    def reset_for_day(self) -> None:
        """Clear all outputs for a new trading day."""
        self.date = date.today().isoformat()
        self.call1_output = None
        self.call3_output = None
        self.triggers_fired = []
        self.trades_executed = []

    def is_current_day(self) -> bool:
        """Check if this state is for today."""
        return self.date == date.today().isoformat()

    def add_trigger(self, trigger_type: str, details: str, tickers: list[str] | None = None) -> None:
        """Record a trigger event."""
        self.triggers_fired.append({
            "trigger_type": trigger_type,
            "details": details,
            "tickers": tickers or [],
        })

    def add_trade(self, trade: dict) -> None:
        """Record an executed trade."""
        self.trades_executed.append(trade)
