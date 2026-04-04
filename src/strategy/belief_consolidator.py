"""Belief Consolidator — merges lessons from sim runs into durable seed beliefs.

After each sim run, calls Claude to analyze the run's lessons + existing seed
beliefs, and produces an updated set of max 5 cross-regime seed beliefs.

Seed beliefs are stored in data/seed_beliefs.md and are intended for live
trading only (not loaded during backtests to avoid contamination).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SEED_BELIEFS_PATH = Path("data/seed_beliefs.md")
MAX_SEED_BELIEFS = 5

# Parse beliefs from markdown: ## Belief Name [regime_count]
BELIEF_PATTERN = re.compile(
    r"^## (.+?) \[regimes: (\d+)\]\n(.*?)(?=\n## |\Z)",
    re.MULTILINE | re.DOTALL,
)


def load_seed_beliefs() -> list[dict]:
    """Load existing seed beliefs from disk."""
    if not SEED_BELIEFS_PATH.exists():
        return []
    content = SEED_BELIEFS_PATH.read_text()
    beliefs = []
    for match in BELIEF_PATTERN.finditer(content):
        beliefs.append({
            "name": match.group(1).strip(),
            "regime_count": int(match.group(2)),
            "description": match.group(3).strip(),
        })
    return beliefs


def save_seed_beliefs(beliefs: list[dict]) -> None:
    """Write seed beliefs to disk."""
    SEED_BELIEFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Seed Beliefs", ""]
    lines.append(
        "Cross-regime investment principles consolidated from multiple simulation runs."
    )
    lines.append(
        "These are loaded for live trading only — never during backtests."
    )
    lines.append("")
    for b in beliefs[:MAX_SEED_BELIEFS]:
        lines.append(f"## {b['name']} [regimes: {b.get('regime_count', 1)}]")
        lines.append(b["description"])
        lines.append("")
    SEED_BELIEFS_PATH.write_text("\n".join(lines))
    logger.info("Saved %d seed beliefs to %s", len(beliefs), SEED_BELIEFS_PATH)


def consolidate_beliefs(
    run_lessons: list[dict],
    run_beliefs: list[dict],
    run_regime: str,
    run_summary: dict,
    claude_client=None,
) -> list[dict]:
    """Consolidate lessons from a sim run into seed beliefs via Claude.

    Args:
        run_lessons: Lessons from the completed sim run
        run_beliefs: Beliefs from the completed sim run (if any)
        run_regime: Market regime label (e.g. "bull", "bear", "correction", "flat")
        run_summary: Dict with period, return, alpha, etc.

    Returns:
        Updated list of seed beliefs (max 5).
    """
    existing = load_seed_beliefs()

    # Format inputs for Claude
    existing_text = _format_existing_beliefs(existing)
    lessons_text = _format_lessons(run_lessons)
    beliefs_text = _format_run_beliefs(run_beliefs)
    summary_text = _format_summary(run_summary, run_regime)

    prompt = f"""You are consolidating investment lessons into durable seed beliefs.

CONTEXT:
{summary_text}

LESSONS FROM THIS RUN:
{lessons_text}

BELIEFS FROM THIS RUN:
{beliefs_text}

EXISTING SEED BELIEFS (from prior runs):
{existing_text}

TASK:
Analyze the lessons and beliefs from this run alongside existing seed beliefs.
Produce an updated set of max {MAX_SEED_BELIEFS} seed beliefs that represent
the most durable, cross-regime investment principles.

RULES:
- Max {MAX_SEED_BELIEFS} seed beliefs total
- A belief should only be promoted if it reflects a principle that would work
  across different market regimes (bull, bear, correction, flat)
- If a new lesson reinforces an existing seed belief, increment its regime_count
- If a new lesson contradicts an existing seed belief, note it and consider removal
- Prefer beliefs about PROCESS (how to trade) over CONTENT (what to trade)
- Each belief should be actionable — a trader could apply it without additional context
- Do NOT include beliefs that are regime-specific (e.g. "buy energy in inflation")

Respond with ONLY valid JSON:
{{
  "seed_beliefs": [
    {{
      "name": "Short descriptive name",
      "regime_count": 2,
      "description": "The full belief text — 1-3 sentences explaining the principle and when to apply it."
    }}
  ],
  "reasoning": "Brief explanation of what changed and why"
}}"""

    # Delegate to Anthropic SDK client if available (live mode)
    if claude_client is not None:
        try:
            data = claude_client.call(prompt, model="sonnet")
            if not data:
                return existing
            beliefs = data.get("seed_beliefs", [])
            reasoning = data.get("reasoning", "")
            if reasoning:
                logger.info("Belief consolidation: %s", reasoning[:200])
            if beliefs:
                save_seed_beliefs(beliefs)
                return beliefs
            return existing
        except Exception as e:
            logger.error("Belief consolidation via SDK failed: %s", e)
            return existing

    # Fallback: subprocess to Claude CLI (sim mode)
    try:
        import os as _os
        cli_env = {k: v for k, v in _os.environ.items() if k != "ANTHROPIC_API_KEY"}
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text", "--model", "sonnet"],
            capture_output=True,
            text=True,
            timeout=120,
            env=cli_env,
        )

        if result.returncode != 0:
            logger.error("Belief consolidation failed: %s", result.stderr[:300])
            return existing

        raw = result.stdout.strip()
        text = raw
        if "```json" in text:
            text = text.split("```json", 1)[1]
            text = text.split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1]
            text = text.split("```", 1)[0]
        text = text.strip()

        data = json.loads(text)
        beliefs = data.get("seed_beliefs", [])
        reasoning = data.get("reasoning", "")

        if reasoning:
            logger.info("Belief consolidation: %s", reasoning[:200])

        if beliefs:
            save_seed_beliefs(beliefs)
            return beliefs

        return existing

    except subprocess.TimeoutExpired:
        logger.error("Belief consolidation timed out")
        return existing
    except json.JSONDecodeError as e:
        logger.error("Failed to parse consolidation response: %s", e)
        return existing
    except FileNotFoundError:
        logger.error("Claude CLI not found for belief consolidation")
        return existing


def classify_regime(report: dict) -> str:
    """Classify the market regime from a sim report."""
    spy_return = report.get("spy_return_pct", 0)
    if spy_return > 10:
        return "bull"
    elif spy_return < -10:
        return "bear"
    elif spy_return < -3:
        return "correction"
    else:
        return "flat"


def _format_existing_beliefs(beliefs: list[dict]) -> str:
    if not beliefs:
        return "(No existing seed beliefs)"
    lines = []
    for b in beliefs:
        lines.append(
            f"- [{b.get('regime_count', 1)} regimes] {b['name']}: {b['description']}"
        )
    return "\n".join(lines)


def _format_lessons(lessons: list[dict]) -> str:
    if not lessons:
        return "(No lessons from this run)"
    lines = []
    for l in lessons:
        content = l["content"] if isinstance(l, dict) else str(l)
        score = l.get("score", "?") if isinstance(l, dict) else "?"
        lines.append(f"- [score {score}/5] {content}")
    return "\n".join(lines)


def _format_run_beliefs(beliefs: list[dict]) -> str:
    if not beliefs:
        return "(No beliefs formed during this run)"
    lines = []
    for b in beliefs:
        lines.append(f"- {b.get('name', 'unnamed')}: {b.get('description', '')}")
    return "\n".join(lines)


def _format_summary(report: dict, regime: str) -> str:
    return (
        f"Sim Period: {report.get('period', 'unknown')}\n"
        f"Market Regime: {regime}\n"
        f"Bot Return: {report.get('total_return_pct', 0):+.1f}%\n"
        f"SPY Return: {report.get('spy_return_pct', 0):+.1f}%\n"
        f"Alpha: {report.get('alpha_pct', 0):+.1f}%\n"
        f"Win Rate: {report.get('win_rate_pct', 0):.0f}%\n"
        f"Total Trades: {report.get('total_trades', 0)}"
    )
