"""Surgical cleanup tool for corrupted live memory files.

Use when the ledger/journal/thesis files drift from reality — e.g. a trade
was journaled but never actually executed, so memory claims something that
didn't happen. Each command reads the target file, applies a precise edit,
and writes it back. Prints what was removed so you have an audit trail.

Typical usage on Railway:

    railway run python -m src.live.cleanup remove-journal-entry \\
        --date 2026-04-10 --ticker MU --action PYRAMID

    railway run python -m src.live.cleanup remove-pyramid-note \\
        --ticker MU --date 2026-04-10

Always run --dry-run first to preview. Files are only touched when you
pass --apply.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from src.config import CONFIG

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    """Resolve the live data directory from config."""
    live_cfg = CONFIG.get("live", {})
    return Path(live_cfg.get("data_dir", "data/live"))


def _journal_path() -> Path:
    return _data_dir() / "decision_journal.md"


def _theses_path() -> Path:
    return _data_dir() / "active_theses.md"


def _confirm_preview(what: str, old: str, new: str, apply: bool) -> bool:
    """Print a diff-like preview and return True if the edit should be written."""
    if old == new:
        print(f"[no change] {what}: nothing to remove")
        return False

    removed_lines = [
        line for line in old.splitlines() if line not in set(new.splitlines())
    ]
    print(f"[would remove] {what}:")
    for line in removed_lines:
        print(f"  - {line}")

    if not apply:
        print("\n(dry-run — re-run with --apply to write changes)")
        return False
    return True


def remove_journal_entry(ticker: str, date_str: str, action: str, apply: bool) -> None:
    """Remove a single `- **ACTION TICKER** (...): ...` line from a date section.

    If the line's removal empties out the whole date section, the date
    header and the trailing `---` separator are cleaned up too.
    """
    path = _journal_path()
    if not path.exists():
        print(f"[error] journal not found: {path}", file=sys.stderr)
        sys.exit(1)

    original = path.read_text(encoding="utf-8")

    # The journal has per-date sections like:
    #   ## 2026-04-10
    #
    #   - **PYRAMID MU** (28%): reasoning…
    #
    #   ---
    #
    # Match the bullet line: `- **<ACTION> <TICKER>**` possibly with (pct):
    ticker_u = ticker.upper()
    action_u = action.upper()
    bullet_re = re.compile(
        rf"^- \*\*{re.escape(action_u)} {re.escape(ticker_u)}\*\*.*$",
        flags=re.MULTILINE,
    )

    # Split into date sections to scope the removal to the requested date.
    sections = re.split(r"(?=^## \d{4}-\d{2}-\d{2}\s*$)", original, flags=re.MULTILINE)
    rebuilt: list[str] = []
    target_header = f"## {date_str}"
    touched = False

    for sec in sections:
        if not sec.strip():
            rebuilt.append(sec)
            continue
        first_line = sec.strip().splitlines()[0]
        if first_line != target_header:
            rebuilt.append(sec)
            continue

        new_sec, count = bullet_re.subn("", sec)
        if count == 0:
            rebuilt.append(sec)
            continue

        # Collapse blank-line runs the removal may have left behind
        new_sec = re.sub(r"\n{3,}", "\n\n", new_sec)

        # If the section has no bullet lines left, drop the whole section
        remaining_bullets = re.findall(r"^- \*\*", new_sec, flags=re.MULTILINE)
        if not remaining_bullets:
            print(f"[info] date section {date_str} now empty — removing entire section")
            new_sec = ""

        rebuilt.append(new_sec)
        touched = True

    if not touched:
        print(f"[no match] no `{action_u} {ticker_u}` bullet found under `## {date_str}`")
        return

    new_content = "".join(rebuilt)
    # Normalise trailing whitespace
    new_content = re.sub(r"\n{3,}", "\n\n", new_content).rstrip() + "\n"

    if _confirm_preview(
        f"journal entry {action_u} {ticker_u} on {date_str}",
        original, new_content, apply,
    ):
        path.write_text(new_content, encoding="utf-8")
        print(f"[applied] wrote {path}")


def remove_pyramid_note(ticker: str, date_str: str, apply: bool) -> None:
    """Strip a `[PYRAMID YYYY-MM-DD → N%] ...` suffix from a thesis body.

    Only affects the block for the given ticker — other theses untouched.
    """
    path = _theses_path()
    if not path.exists():
        print(f"[error] theses file not found: {path}", file=sys.stderr)
        sys.exit(1)

    original = path.read_text(encoding="utf-8")
    ticker_u = ticker.upper()

    # Pyramid notes are appended inline by ThesisManager.append_pyramid_note:
    #   "... [PYRAMID 2026-04-10 → 28%] <reasoning>"
    # The reasoning can span to the end of its line. We strip the bracketed
    # tag plus any text until the line ends or the next [PYRAMID ...] tag.
    pyramid_re = re.compile(
        rf"\s*\[PYRAMID {re.escape(date_str)}(?:\s*→\s*\d+\.?\d*%)?\]"
        r"[^\[\n]*",
    )

    # Split theses file into per-ticker blocks. Each block starts with
    # `### <TICKER> ...` (adjust if your format differs).
    block_re = re.compile(r"(?=^###\s+\S+)", flags=re.MULTILINE)
    blocks = block_re.split(original)
    rebuilt: list[str] = []
    touched = False

    for block in blocks:
        header_match = re.match(r"^###\s+(\S+)", block)
        if not header_match or header_match.group(1).upper() != ticker_u:
            rebuilt.append(block)
            continue

        new_block, count = pyramid_re.subn("", block)
        if count:
            touched = True
            print(f"[info] stripped {count} pyramid note(s) from {ticker_u}")
        rebuilt.append(new_block)

    if not touched:
        print(f"[no match] no pyramid note dated {date_str} found on {ticker_u}")
        return

    new_content = "".join(rebuilt)

    if _confirm_preview(
        f"pyramid note on {ticker_u} dated {date_str}",
        original, new_content, apply,
    ):
        path.write_text(new_content, encoding="utf-8")
        print(f"[applied] wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.live.cleanup",
        description="Surgical cleanup for corrupted live memory files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    j = sub.add_parser(
        "remove-journal-entry",
        help="Remove a single bullet from decision_journal.md",
    )
    j.add_argument("--date", required=True, help="YYYY-MM-DD")
    j.add_argument("--ticker", required=True)
    j.add_argument("--action", required=True, help="e.g. BUY, PYRAMID, CLOSE")
    j.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")

    p = sub.add_parser(
        "remove-pyramid-note",
        help="Strip a [PYRAMID date → pct%] suffix from a thesis body",
    )
    p.add_argument("--ticker", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD of the pyramid note")
    p.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command == "remove-journal-entry":
        remove_journal_entry(args.ticker, args.date, args.action, args.apply)
    elif args.command == "remove-pyramid-note":
        remove_pyramid_note(args.ticker, args.date, args.apply)


if __name__ == "__main__":
    main()
