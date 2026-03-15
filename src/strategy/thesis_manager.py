"""Thesis-driven memory system — persistent state for V3 investment decisions.

Manages markdown files that give Claude continuity across stateless calls:
- active_theses.md — current investment theses (max 15)
- portfolio_ledger.md — what we hold right now
- quarterly_summaries.md — compressed history (max 8)
- lessons_learned.md — scored short-term rules (max 15, scored 1-5)
- beliefs.md — long-term principles consolidated from lessons (max 5)
- themes.md — investment themes (scored 1-5)
- simulation_log.md — backtest history (excluded from decision context)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from src.config import CONFIG

logger = logging.getLogger(__name__)

# --- Parsing patterns ---
THESIS_HEADER = re.compile(r"^## ([A-Z0-9.]+) — ", re.MULTILINE)
QUARTERLY_HEADER = re.compile(r"^## Q\d \d{4}", re.MULTILINE)
LESSON_HEADER = re.compile(r"^## Lesson (\d+) \[(\d)\]", re.MULTILINE)
LESSON_HEADER_OLD = re.compile(r"^## Lesson (\d+)\s*$", re.MULTILINE)
SIM_RUN_HEADER = re.compile(r"^## Run ", re.MULTILINE)
THEME_HEADER = re.compile(r"^## (.+?) \[(\d)\]$", re.MULTILINE)
BELIEF_HEADER = re.compile(r"^## (.+?) \[(\d)\]$", re.MULTILINE)
LEDGER_ROW = re.compile(
    r"^\|\s*([A-Z0-9.]+)\s*\|"
    r"\s*(LONG|SHORT)\s*\|"
    r"\s*([\d.]+)\s*\|"
    r"\s*\$([\d.,]+)\s*\|"
    r"\s*\$([\d.,]+)\s*\|"
    r"\s*\$([\d.,]+)\s*\|"
    r"\s*([+-]?[\d.,]+)%\s*\|"
    r"\s*([\d-]+)\s*\|",
    re.MULTILINE,
)
# Backwards-compatible: match old 6-column ledger rows too
LEDGER_ROW_V1 = re.compile(
    r"^\|\s*([A-Z0-9.]+)\s*\|"
    r"\s*(LONG|SHORT)\s*\|"
    r"\s*([\d.]+)\s*\|"
    r"\s*\$([\d.,]+)\s*\|"
    r"\s*\$([\d.,]+)\s*\|"
    r"\s*([\d-]+)\s*\|",
    re.MULTILINE,
)


def _mem_cfg(key: str, default):
    return CONFIG.get("memory", {}).get(key, default)


class ThesisManager:
    """Single manager for all V3 memory files."""

    def __init__(self, base_dir: str | Path | None = None):
        root = Path(base_dir) if base_dir else Path(".")
        self._paths = {
            "theses": root / _mem_cfg("theses_path", "data/active_theses.md"),
            "ledger": root / _mem_cfg("ledger_path", "data/portfolio_ledger.md"),
            "summaries": root / _mem_cfg("summaries_path", "data/quarterly_summaries.md"),
            "lessons": root / _mem_cfg("lessons_path", "data/lessons_learned.md"),
            "sim_log": root / _mem_cfg("sim_log_path", "data/simulation_log.md"),
            "themes": root / _mem_cfg("themes_path", "data/themes.md"),
            "beliefs": root / _mem_cfg("beliefs_path", "data/beliefs.md"),
        }
        self._max_theses = _mem_cfg("max_active_theses", 15)
        self._max_summaries = _mem_cfg("max_quarterly_summaries", 8)
        self._max_themes = _mem_cfg("max_themes", 8)
        self._max_lessons = _mem_cfg("max_lessons", 15)
        self._max_beliefs = _mem_cfg("max_beliefs", 5)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read(self, key: str) -> str:
        path = self._paths[key]
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _write(self, key: str, content: str) -> None:
        path = self._paths[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Active Theses
    # ------------------------------------------------------------------

    def get_all_theses(self) -> list[dict]:
        """Parse active_theses.md into a list of thesis dicts."""
        content = self._read("theses")
        if not content.strip():
            return []

        parts = THESIS_HEADER.split(content)
        # parts: [preamble, ticker1, body1, ticker2, body2, ...]
        theses = []
        for i in range(1, len(parts) - 1, 2):
            ticker = parts[i]
            body = parts[i + 1]
            theses.append(self._parse_thesis(ticker, body))
        return theses

    def get_by_ticker(self, ticker: str) -> dict | None:
        ticker = ticker.upper()
        for t in self.get_all_theses():
            if t["ticker"] == ticker:
                return t
        return None

    def add_thesis(
        self,
        ticker: str,
        direction: str,
        thesis: str,
        entry_price: float,
        target_price: float,
        stop_price: float,
        timeframe: str = "",
        confidence: str = "medium",
    ) -> bool:
        """Add a new thesis. Returns False if at max capacity."""
        ticker = ticker.upper()
        existing = self.get_all_theses()

        # Update if ticker already exists
        for t in existing:
            if t["ticker"] == ticker:
                return self._update_thesis_in_list(
                    existing, ticker, direction=direction, thesis=thesis,
                    entry_price=entry_price, target_price=target_price,
                    stop_price=stop_price, timeframe=timeframe, confidence=confidence,
                )

        if len(existing) >= self._max_theses:
            logger.warning("Cannot add thesis for %s — at max capacity (%d)", ticker, self._max_theses)
            return False

        entry = self._format_thesis(
            ticker, direction, thesis, entry_price,
            target_price, stop_price, timeframe, confidence,
            date_added=datetime.utcnow().strftime("%Y-%m-%d"),
        )
        content = self._read("theses")
        if content and not content.endswith("\n"):
            content += "\n"
        content += entry
        self._write("theses", content)
        logger.debug("Added thesis for %s", ticker)
        return True

    def update_thesis(self, ticker: str, **updates) -> bool:
        """Update fields on an existing thesis."""
        ticker = ticker.upper()
        existing = self.get_all_theses()
        return self._update_thesis_in_list(existing, ticker, **updates)

    def remove_thesis(self, ticker: str) -> bool:
        """Remove a thesis by ticker."""
        ticker = ticker.upper()
        existing = self.get_all_theses()
        filtered = [t for t in existing if t["ticker"] != ticker]
        if len(filtered) == len(existing):
            return False
        self._rebuild_theses(filtered)
        logger.debug("Removed thesis for %s", ticker)
        return True

    def _update_thesis_in_list(self, theses: list[dict], ticker: str, **updates) -> bool:
        found = False
        for t in theses:
            if t["ticker"] == ticker:
                t.update({k: v for k, v in updates.items() if v is not None})
                found = True
                break
        if not found:
            return False
        self._rebuild_theses(theses)
        return True

    def _rebuild_theses(self, theses: list[dict]) -> None:
        parts = ["# Active Theses\n"]
        for t in theses:
            parts.append(self._format_thesis(
                t["ticker"], t.get("direction", "LONG"), t.get("thesis", ""),
                t.get("entry_price", 0), t.get("target_price", 0),
                t.get("stop_price", 0), t.get("timeframe", ""),
                t.get("confidence", "medium"), t.get("date_added", ""),
                t.get("status", "active"),
            ))
        self._write("theses", "\n".join(parts))

    @staticmethod
    def _format_thesis(
        ticker, direction, thesis, entry_price,
        target_price, stop_price, timeframe, confidence,
        date_added="", status="active",
    ) -> str:
        lines = [
            f"## {ticker} — {direction.upper()}",
            f"**Thesis:** {thesis}",
            f"**Entry:** ${entry_price:.2f} | **Target:** ${target_price:.2f} | **Stop:** ${stop_price:.2f}",
            f"**Timeframe:** {timeframe} | **Confidence:** {confidence}",
            f"**Status:** {status} | **Added:** {date_added}",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def _parse_thesis(ticker: str, body: str) -> dict:
        def _extract(pattern, text, default=""):
            m = re.search(pattern, text)
            return m.group(1).strip() if m else default

        def _extract_float(pattern, text, default=0.0):
            m = re.search(pattern, text)
            return float(m.group(1).replace(",", "")) if m else default

        # The body starts after the " — DIRECTION\n" part
        direction_match = re.match(r"(\w+)\n", body)
        direction = direction_match.group(1) if direction_match else "LONG"

        return {
            "ticker": ticker,
            "direction": direction,
            "thesis": _extract(r"\*\*Thesis:\*\*\s*(.+)", body),
            "entry_price": _extract_float(r"\*\*Entry:\*\*\s*\$([\d.,]+)", body),
            "target_price": _extract_float(r"\*\*Target:\*\*\s*\$([\d.,]+)", body),
            "stop_price": _extract_float(r"\*\*Stop:\*\*\s*\$([\d.,]+)", body),
            "timeframe": _extract(r"\*\*Timeframe:\*\*\s*([^|]+)", body),
            "confidence": _extract(r"\*\*Confidence:\*\*\s*(\w+)", body, "medium"),
            "status": _extract(r"\*\*Status:\*\*\s*(\w+)", body, "active"),
            "date_added": _extract(r"\*\*Added:\*\*\s*([\d-]+)", body),
        }

    # ------------------------------------------------------------------
    # Portfolio Ledger
    # ------------------------------------------------------------------

    def get_holdings(self) -> list[dict]:
        """Parse portfolio_ledger.md into a list of position dicts."""
        content = self._read("ledger")
        holdings = []
        # Try new 8-column format first
        for m in LEDGER_ROW.finditer(content):
            holdings.append({
                "ticker": m.group(1),
                "side": m.group(2),
                "qty": float(m.group(3)),
                "entry_price": float(m.group(4).replace(",", "")),
                "current_value": float(m.group(5).replace(",", "")),
                "current_price": float(m.group(6).replace(",", "")),
                "pnl_pct": float(m.group(7).replace(",", "")),
                "date_opened": m.group(8),
            })
        if holdings:
            return holdings
        # Fall back to old 6-column format
        for m in LEDGER_ROW_V1.finditer(content):
            entry_price = float(m.group(4).replace(",", ""))
            qty = float(m.group(3))
            current_value = float(m.group(5).replace(",", ""))
            current_price = current_value / qty if qty > 0 else 0.0
            cost_basis = entry_price * qty
            pnl_pct = ((current_value - cost_basis) / cost_basis * 100) if cost_basis > 0 else 0.0
            holdings.append({
                "ticker": m.group(1),
                "side": m.group(2),
                "qty": qty,
                "entry_price": entry_price,
                "current_value": current_value,
                "current_price": current_price,
                "pnl_pct": pnl_pct,
                "date_opened": m.group(6),
            })
        return holdings

    def update_position(
        self, ticker: str, side: str, qty: float,
        entry_price: float, current_value: float, date_opened: str,
    ) -> None:
        """Add or update a position in the ledger."""
        ticker = ticker.upper()
        holdings = self.get_holdings()
        updated = False
        for h in holdings:
            if h["ticker"] == ticker:
                h["side"] = side.upper()
                h["qty"] = qty
                h["entry_price"] = entry_price
                h["current_value"] = current_value
                h["date_opened"] = date_opened
                updated = True
                break
        if not updated:
            holdings.append({
                "ticker": ticker, "side": side.upper(), "qty": qty,
                "entry_price": entry_price, "current_value": current_value,
                "date_opened": date_opened,
            })
        self._rebuild_ledger(holdings)

    def remove_position(self, ticker: str) -> bool:
        ticker = ticker.upper()
        holdings = self.get_holdings()
        filtered = [h for h in holdings if h["ticker"] != ticker]
        if len(filtered) == len(holdings):
            return False
        self._rebuild_ledger(filtered)
        return True

    def update_values(self, updates: dict[str, float]) -> None:
        """Batch update current_value and current_price for multiple tickers."""
        holdings = self.get_holdings()
        for h in holdings:
            if h["ticker"] in updates:
                h["current_value"] = updates[h["ticker"]]
                qty = h["qty"]
                if qty > 0:
                    h["current_price"] = updates[h["ticker"]] / qty
        self._rebuild_ledger(holdings)

    def _rebuild_ledger(self, holdings: list[dict]) -> None:
        lines = [
            "# Portfolio Ledger",
            "",
            "| Ticker | Side | Qty | Entry Price | Current Value | Current Price | P&L % | Date Opened |",
            "|--------|------|-----|-------------|---------------|---------------|-------|-------------|",
        ]
        for h in holdings:
            qty = h["qty"]
            entry_price = h["entry_price"]
            current_value = h["current_value"]
            current_price = h.get("current_price", current_value / qty if qty > 0 else 0.0)
            cost_basis = entry_price * qty
            if h["side"] == "SHORT":
                pnl_pct = ((cost_basis - current_value) / cost_basis * 100) if cost_basis > 0 else 0.0
            else:
                pnl_pct = ((current_value - cost_basis) / cost_basis * 100) if cost_basis > 0 else 0.0
            lines.append(
                f"| {h['ticker']} | {h['side']} | {qty} | "
                f"${entry_price:,.2f} | ${current_value:,.2f} | "
                f"${current_price:,.2f} | {pnl_pct:+.1f}% | {h['date_opened']} |"
            )
        lines.append("")
        self._write("ledger", "\n".join(lines))

    # ------------------------------------------------------------------
    # Quarterly Summaries
    # ------------------------------------------------------------------

    def get_recent_summaries(self, n: int | None = None) -> list[str]:
        """Get quarterly summary entries."""
        content = self._read("summaries")
        if not content.strip():
            return []
        parts = QUARTERLY_HEADER.split(content)
        headers = QUARTERLY_HEADER.findall(content)
        entries = []
        for i, header in enumerate(headers):
            body = parts[i + 1] if i + 1 < len(parts) else ""
            entries.append(f"{header}{body}".strip())
        limit = n or self._max_summaries
        return entries[-limit:]

    def append_summary(self, quarter: str, year: int, body: str) -> None:
        """Append a quarterly summary and truncate to max."""
        entry = f"## {quarter} {year}\n{body}\n\n---\n"
        content = self._read("summaries")
        if content and not content.endswith("\n"):
            content += "\n"
        content += entry
        self._write("summaries", content)
        self._truncate_summaries()

    def _truncate_summaries(self) -> None:
        entries = self.get_recent_summaries()
        if len(entries) <= self._max_summaries:
            return
        kept = entries[-self._max_summaries:]
        self._write("summaries", "\n\n".join(kept) + "\n")
        logger.debug("Truncated quarterly summaries to %d", self._max_summaries)

    # ------------------------------------------------------------------
    # Lessons Learned (scored 1-5, max 15)
    # ------------------------------------------------------------------

    def get_all_lessons(self) -> list[dict]:
        """Parse lessons_learned.md into a list of lesson dicts.

        Returns list of {"number": int, "score": int, "content": str}.
        Backward compatible: old format (no score) treated as score 3.
        """
        content = self._read("lessons")
        if not content.strip():
            return []

        lessons = []

        # Try new scored format first: ## Lesson N [S]
        new_matches = list(LESSON_HEADER.finditer(content))
        if new_matches:
            for idx, m in enumerate(new_matches):
                number = int(m.group(1))
                score = int(m.group(2))
                start = m.end()
                end = new_matches[idx + 1].start() if idx + 1 < len(new_matches) else len(content)
                body = content[start:end].strip()
                # Remove trailing ---
                if body.endswith("---"):
                    body = body[:-3].strip()
                lessons.append({"number": number, "score": score, "content": body})
            return lessons

        # Fall back to old format: ## Lesson N (no score) — treat as score 3
        old_matches = list(LESSON_HEADER_OLD.finditer(content))
        for idx, m in enumerate(old_matches):
            number = int(m.group(1))
            start = m.end()
            end = old_matches[idx + 1].start() if idx + 1 < len(old_matches) else len(content)
            body = content[start:end].strip()
            if body.endswith("---"):
                body = body[:-3].strip()
            lessons.append({"number": number, "score": 3, "content": body})
        return lessons

    def append_lesson(self, lesson: str) -> None:
        """Add a new lesson with score 1. If at max, remove lowest-scored (oldest tie-break)."""
        existing = self.get_all_lessons()

        if len(existing) >= self._max_lessons:
            # Remove lowest-scored lesson (tie-break: oldest = lowest number)
            lowest = min(existing, key=lambda l: (l["score"], -l["number"]))
            existing = [l for l in existing if l["number"] != lowest["number"]]
            logger.debug("Evicted lesson %d (score %d) to make room", lowest["number"], lowest["score"])

        number = max((l["number"] for l in existing), default=0) + 1
        existing.append({"number": number, "score": 1, "content": lesson})
        self._rebuild_lessons(existing)

    def increment_lesson_score(self, lesson_number: int) -> bool:
        """Bump a lesson's score by 1, capped at 5."""
        lessons = self.get_all_lessons()
        for l in lessons:
            if l["number"] == lesson_number:
                l["score"] = min(5, l["score"] + 1)
                self._rebuild_lessons(lessons)
                return True
        return False

    def decrement_lesson_score(self, lesson_number: int) -> bool:
        """Reduce a lesson's score by 1. Auto-remove if score drops below 1."""
        lessons = self.get_all_lessons()
        found = False
        for l in lessons:
            if l["number"] == lesson_number:
                l["score"] -= 1
                found = True
                break
        if not found:
            return False

        # Remove lessons with score < 1
        lessons = [l for l in lessons if l["score"] >= 1]
        self._rebuild_lessons(lessons)
        return True

    def remove_lesson(self, lesson_number: int) -> bool:
        """Remove a lesson by number and renumber remaining."""
        lessons = self.get_all_lessons()
        filtered = [l for l in lessons if l["number"] != lesson_number]
        if len(filtered) == len(lessons):
            return False
        # Renumber
        for i, l in enumerate(filtered, 1):
            l["number"] = i
        self._rebuild_lessons(filtered)
        return True

    def _rebuild_lessons(self, lessons: list[dict]) -> None:
        """Rebuild the lessons file from a list of lesson dicts."""
        lines = ["# Lessons Learned\n"]
        for l in lessons:
            lines.append(f"## Lesson {l['number']} [{l['score']}]")
            lines.append(l["content"])
            lines.append("")
            lines.append("---")
            lines.append("")
        self._write("lessons", "\n".join(lines))

    # ------------------------------------------------------------------
    # Beliefs (long-term principles, max 5, scored 1-5)
    # ------------------------------------------------------------------

    def get_all_beliefs(self) -> list[dict]:
        """Parse beliefs.md into a list of {name, score, description, supporting_lessons} dicts."""
        content = self._read("beliefs")
        if not content.strip():
            return []

        beliefs = []
        # Use same header pattern as themes: ## Name [score]
        parts = BELIEF_HEADER.split(content)
        # parts: [preamble, name1, score1, body1, name2, score2, body2, ...]
        for i in range(1, len(parts) - 2, 3):
            name = parts[i]
            score = int(parts[i + 1])
            body = parts[i + 2].strip()
            # Extract description and supporting lessons
            desc = ""
            supporting = []
            for line in body.split("\n"):
                line = line.strip()
                if line.startswith("Supported by:"):
                    # Parse "Supported by: Lessons 3, 7, 12"
                    refs = line.replace("Supported by:", "").strip()
                    refs = refs.replace("Lessons", "").replace("Lesson", "").strip()
                    for ref in refs.split(","):
                        ref = ref.strip()
                        if ref.isdigit():
                            supporting.append(int(ref))
                elif line and not line.startswith("---"):
                    if not desc:
                        desc = line
            beliefs.append({
                "name": name,
                "score": score,
                "description": desc,
                "supporting_lessons": supporting,
            })
        return beliefs

    def add_belief(self, name: str, description: str, supporting_lessons: list[int] | None = None) -> bool:
        """Add a new belief. Returns False if at max capacity or already exists."""
        existing = self.get_all_beliefs()

        # Update if already exists
        for b in existing:
            if b["name"].lower() == name.lower():
                b["description"] = description
                if supporting_lessons is not None:
                    b["supporting_lessons"] = supporting_lessons
                self._rebuild_beliefs(existing)
                return True

        if len(existing) >= self._max_beliefs:
            logger.warning("Cannot add belief '%s' — at max capacity (%d)", name, self._max_beliefs)
            return False

        existing.append({
            "name": name,
            "score": 3,
            "description": description,
            "supporting_lessons": supporting_lessons or [],
        })
        self._rebuild_beliefs(existing)
        logger.debug("Added belief: %s", name)
        return True

    def update_belief(self, name: str, description: str | None = None, supporting_lessons: list[int] | None = None) -> bool:
        """Update an existing belief's description and/or supporting lessons."""
        existing = self.get_all_beliefs()
        found = False
        for b in existing:
            if b["name"].lower() == name.lower():
                if description is not None:
                    b["description"] = description
                if supporting_lessons is not None:
                    b["supporting_lessons"] = supporting_lessons
                found = True
                break
        if not found:
            return False
        self._rebuild_beliefs(existing)
        return True

    def remove_belief(self, name: str) -> bool:
        """Remove a belief by name."""
        existing = self.get_all_beliefs()
        filtered = [b for b in existing if b["name"].lower() != name.lower()]
        if len(filtered) == len(existing):
            return False
        self._rebuild_beliefs(filtered)
        return True

    def _rebuild_beliefs(self, beliefs: list[dict]) -> None:
        lines = ["# Investment Beliefs\n"]
        for b in beliefs:
            lines.append(f"## {b['name']} [{b.get('score', 3)}]")
            lines.append(b["description"])
            if b.get("supporting_lessons"):
                refs = ", ".join(str(n) for n in b["supporting_lessons"])
                lines.append(f"Supported by: Lessons {refs}")
            lines.append("")
            lines.append("---")
            lines.append("")
        self._write("beliefs", "\n".join(lines))

    # ------------------------------------------------------------------
    # Themes (scored 1-5, auto-remove at 1)
    # ------------------------------------------------------------------

    def get_all_themes(self) -> list[dict]:
        """Parse themes.md into a list of {name, description, score} dicts."""
        content = self._read("themes")
        if not content.strip():
            return []

        themes = []
        parts = THEME_HEADER.split(content)
        # parts: [preamble, name1, score1, body1, name2, score2, body2, ...]
        for i in range(1, len(parts) - 2, 3):
            name = parts[i]
            score = int(parts[i + 1])
            body = parts[i + 2].strip()
            # Extract description from body
            desc = ""
            for line in body.split("\n"):
                line = line.strip()
                if line and not line.startswith("---"):
                    desc = line
                    break
            themes.append({"name": name, "description": desc, "score": score})
        return themes

    def get_theme(self, name: str) -> dict | None:
        for t in self.get_all_themes():
            if t["name"].lower() == name.lower():
                return t
        return None

    def add_theme(self, name: str, description: str, score: int = 3) -> bool:
        """Add a new theme. Returns False if at max capacity or already exists."""
        existing = self.get_all_themes()

        # Update if already exists
        for t in existing:
            if t["name"].lower() == name.lower():
                t["description"] = description
                t["score"] = score
                self._rebuild_themes(existing)
                return True

        if len(existing) >= self._max_themes:
            logger.warning("Cannot add theme '%s' — at max capacity (%d)", name, self._max_themes)
            return False

        score = max(1, min(5, score))
        existing.append({"name": name, "description": description, "score": score})
        self._rebuild_themes(existing)
        logger.debug("Added theme: %s [%d]", name, score)
        return True

    def update_theme_score(self, name: str, delta: int) -> bool:
        """Adjust a theme's score by delta (clamped 1-5). Removes if score hits 1."""
        existing = self.get_all_themes()
        found = False
        for t in existing:
            if t["name"].lower() == name.lower():
                t["score"] = max(1, min(5, t["score"] + delta))
                found = True
                break

        if not found:
            return False

        # Auto-remove themes at score 1
        existing = [t for t in existing if t["score"] > 1]
        self._rebuild_themes(existing)
        return True

    def remove_theme(self, name: str) -> bool:
        existing = self.get_all_themes()
        filtered = [t for t in existing if t["name"].lower() != name.lower()]
        if len(filtered) == len(existing):
            return False
        self._rebuild_themes(filtered)
        return True

    def _rebuild_themes(self, themes: list[dict]) -> None:
        lines = ["# Investment Themes\n"]
        for t in themes:
            lines.append(f"## {t['name']} [{t['score']}]")
            lines.append(t["description"])
            lines.append("")
            lines.append("---")
            lines.append("")
        self._write("themes", "\n".join(lines))

    # ------------------------------------------------------------------
    # Simulation Log
    # ------------------------------------------------------------------

    def get_all_sim_runs(self) -> list[str]:
        content = self._read("sim_log")
        if not content.strip():
            return []
        parts = SIM_RUN_HEADER.split(content)
        headers = SIM_RUN_HEADER.findall(content)
        entries = []
        for i, header in enumerate(headers):
            body = parts[i + 1] if i + 1 < len(parts) else ""
            entries.append(f"{header}{body}".strip())
        return entries

    def append_sim_run(self, run_id: str, body: str) -> None:
        entry = f"## Run {run_id}\n{body}\n\n---\n"
        content = self._read("sim_log")
        if content and not content.endswith("\n"):
            content += "\n"
        content += entry
        self._write("sim_log", content)

    # ------------------------------------------------------------------
    # Decision Context (main interface for the decision engine)
    # ------------------------------------------------------------------

    def get_decision_context(self) -> str:
        """Return all in-sim memory files formatted for Claude's prompt.

        Excludes simulation_log — that's for post-hoc analysis only.
        """
        sections = []

        # Themes
        themes = self.get_all_themes()
        if themes:
            theme_lines = [f"- {t['name']} [{t['score']}/5]: {t['description']}" for t in themes]
            sections.append("### Investment Themes\n" + "\n".join(theme_lines))
        else:
            sections.append("### Investment Themes\n(No themes set)")

        # Active theses
        theses_content = self._read("theses")
        if theses_content.strip():
            sections.append(f"### Active Theses\n{theses_content.strip()}")
        else:
            sections.append("### Active Theses\n(No active theses)")

        # Portfolio ledger
        ledger_content = self._read("ledger")
        if ledger_content.strip():
            sections.append(f"### Portfolio Ledger\n{ledger_content.strip()}")
        else:
            sections.append("### Portfolio Ledger\n(No current holdings)")

        # Quarterly summaries
        summaries = self.get_recent_summaries()
        if summaries:
            sections.append("### Quarterly Summaries\n" + "\n\n".join(summaries))
        else:
            sections.append("### Quarterly Summaries\n(No history yet)")

        # Beliefs (above lessons — long-term principles)
        beliefs = self.get_all_beliefs()
        if beliefs:
            belief_lines = []
            for b in beliefs:
                refs = ""
                if b.get("supporting_lessons"):
                    refs = f" (from lessons {', '.join(str(n) for n in b['supporting_lessons'])})"
                belief_lines.append(f"- {b['name']} [{b.get('score', 3)}/5]: {b['description']}{refs}")
            sections.append("### Investment Beliefs (Core Principles)\n" + "\n".join(belief_lines))
        else:
            sections.append("### Investment Beliefs (Core Principles)\n(No beliefs established yet)")

        # Lessons learned
        lessons = self.get_all_lessons()
        if lessons:
            lesson_lines = []
            for l in lessons:
                lesson_lines.append(f"## Lesson {l['number']} [score {l['score']}/5]\n{l['content']}")
            sections.append("### Lessons Learned\n" + "\n\n".join(lesson_lines))
        else:
            sections.append("### Lessons Learned\n(No lessons yet)")

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """Clear in-sim memory files. Preserves simulation_log across runs."""
        for key in self._paths:
            if key == "sim_log":
                continue
            path = self._paths[key]
            if path.exists():
                path.unlink()
