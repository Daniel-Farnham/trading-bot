"""Strategy journal — persistent memory across Claude review sessions.

The journal is a markdown file that grows over time. Each review (daily or weekly)
appends an entry with market observations, reasoning, and decision tracking.
This gives Claude continuity across stateless `claude -p` calls.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from src.config import CONFIG

logger = logging.getLogger(__name__)

# Matches "## Review — " at the start of a line (each entry header)
ENTRY_PATTERN = re.compile(r"^## Review — ", re.MULTILINE)


class StrategyJournal:
    """Manages the strategy journal file."""

    def __init__(self, path: str | Path | None = None, max_entries: int | None = None):
        default_path = CONFIG.get("adaptation", {}).get(
            "journal_path", "data/strategy_journal.md"
        )
        self._path = Path(path or default_path)
        self._max_entries = max_entries or CONFIG.get("adaptation", {}).get(
            "journal_max_entries", 20
        )

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> str:
        """Read the full journal content. Returns empty string if file doesn't exist."""
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding="utf-8")

    def get_entries(self) -> list[str]:
        """Parse journal into individual entries."""
        content = self.read()
        if not content.strip():
            return []

        # Split on entry headers, keeping the header with each entry
        parts = ENTRY_PATTERN.split(content)
        entries = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            entries.append(f"## Review — {part}")

        return entries

    def append_entry(
        self,
        date: str,
        review_type: str,
        portfolio_value: float,
        total_return_pct: float,
        cash: float,
        positions_count: int,
        trades_total: int,
        win_rate: float,
        changes: list[dict],
        analysis: str = "",
        tracking_notes: str = "",
    ) -> None:
        """Append a new journal entry and truncate if needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        label = "Weekly Strategic" if review_type == "weekly" else "Daily Tactical"

        # Build the entry
        lines = [
            f"## Review — {date} | {label}",
            "",
            f"**Portfolio:** ${portfolio_value:,.2f} ({total_return_pct:+.1f}%) | "
            f"{positions_count} positions | Cash: ${cash:,.2f}",
            f"**Trade Stats:** {trades_total} total trades | {win_rate:.0f}% win rate",
            "",
        ]

        if analysis:
            lines.append(f"**Analysis:** {analysis}")
            lines.append("")

        if changes:
            lines.append("**Changes Made:**")
            for c in changes:
                lines.append(
                    f"- {c['param']}: {c.get('old_value', '?')} → {c.get('new_value', '?')} "
                    f"({c.get('reason', 'no reason given')})"
                )
            lines.append("")
        else:
            lines.append("**Changes Made:** None — strategy unchanged.")
            lines.append("")

        if tracking_notes:
            lines.append(f"**Tracking Previous Decisions:** {tracking_notes}")
            lines.append("")

        lines.append("---")
        lines.append("")

        entry_text = "\n".join(lines)

        # Append to file
        existing = self.read()
        if existing and not existing.endswith("\n"):
            existing += "\n"

        with open(self._path, "w", encoding="utf-8") as f:
            f.write(existing + entry_text)

        # Truncate to max entries
        self._truncate()

        logger.debug("Journal entry appended for %s (%s)", date, label)

    def _truncate(self) -> None:
        """Keep only the most recent max_entries entries."""
        entries = self.get_entries()
        if len(entries) <= self._max_entries:
            return

        # Keep header (if any non-entry content exists before first entry) + recent entries
        content = self.read()
        first_entry_pos = content.find("## Review — ")

        header = ""
        if first_entry_pos > 0:
            header = content[:first_entry_pos]

        kept = entries[-self._max_entries:]
        truncated = header + "\n".join(kept)

        with open(self._path, "w", encoding="utf-8") as f:
            f.write(truncated)

        logger.debug(
            "Journal truncated: kept %d of %d entries",
            self._max_entries, len(entries),
        )

    def clear(self) -> None:
        """Clear the journal (used for simulation resets)."""
        if self._path.exists():
            self._path.unlink()

    def get_recent_context(self, max_entries: int | None = None) -> str:
        """Get recent journal entries formatted for inclusion in a Claude prompt."""
        entries = self.get_entries()
        if not entries:
            return "(No previous reviews — this is the first review.)"

        n = max_entries or self._max_entries
        recent = entries[-n:]
        return "\n\n".join(recent)
