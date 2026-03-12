from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime

from src.config import CONFIG


DEFAULT_THEMES = [
    {
        "name": "climate_transition",
        "description": "Shift toward renewable energy, EVs, and sustainable practices. Carbon regulation tightening globally.",
        "bullish_sectors": ["clean energy", "EVs", "battery", "solar", "wind", "grid infrastructure"],
        "bearish_sectors": ["coal", "oil exploration", "fossil fuel"],
    },
    {
        "name": "wealth_inequality",
        "description": "Rising wealth gap in developed nations, especially US. Benefits luxury at the top and discount/value at the bottom.",
        "bullish_sectors": ["luxury goods", "wealth management", "fintech", "discount retail", "dollar stores"],
        "bearish_sectors": ["mid-market retail"],
    },
    {
        "name": "aging_populations",
        "description": "Developed world demographics shifting older. Healthcare, pharma, senior living demand rising.",
        "bullish_sectors": ["healthcare", "pharma", "biotech", "senior living", "medical devices", "insurance"],
        "bearish_sectors": ["youth-oriented consumer"],
    },
    {
        "name": "ai_automation",
        "description": "AI and automation transforming industries. Semiconductor demand, cloud compute, and AI software booming.",
        "bullish_sectors": ["semiconductors", "cloud computing", "AI software", "robotics", "data centers"],
        "bearish_sectors": ["manual labor services", "legacy software"],
    },
]


@dataclass
class ThemeAlignment:
    ticker: str
    theme_name: str
    score: float  # -1 (bearish) to +1 (bullish)
    reasoning: str


@dataclass
class Theme:
    name: str
    description: str
    bullish_sectors: list[str] = field(default_factory=list)
    bearish_sectors: list[str] = field(default_factory=list)
    active: bool = True
    discovered_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "bullish_sectors": self.bullish_sectors,
            "bearish_sectors": self.bearish_sectors,
            "active": self.active,
            "discovered_at": self.discovered_at,
        }


class ThemeManager:
    def __init__(self, themes: list[Theme] | None = None):
        if themes is not None:
            self._themes = list(themes)
        else:
            self._themes = [
                Theme(**t) for t in DEFAULT_THEMES
            ]
        self._alignments: dict[str, list[ThemeAlignment]] = {}

    @property
    def active_themes(self) -> list[Theme]:
        return [t for t in self._themes if t.active]

    def add_theme(self, theme: Theme) -> None:
        existing = [t for t in self._themes if t.name == theme.name]
        if not existing:
            self._themes.append(theme)

    def remove_theme(self, name: str) -> None:
        self._themes = [t for t in self._themes if t.name != name]

    def deactivate_theme(self, name: str) -> None:
        for t in self._themes:
            if t.name == name:
                t.active = False

    def get_alignments(self, ticker: str) -> list[ThemeAlignment]:
        return self._alignments.get(ticker, [])

    def get_composite_score(self, ticker: str) -> float:
        """Returns average theme alignment score for a ticker.

        Score ranges from -1 (strongly against themes) to +1 (strongly aligned).
        Returns 0.0 if no alignments exist.
        """
        alignments = self.get_alignments(ticker)
        if not alignments:
            return 0.0
        return sum(a.score for a in alignments) / len(alignments)

    def apply_theme_nudge(self, ticker: str, base_confidence: float) -> float:
        """Applies a soft nudge to signal confidence based on theme alignment.

        A +1.0 theme score boosts confidence by 20%.
        A -1.0 theme score reduces confidence by 20%.
        Result is clamped to [0.0, 1.0].
        """
        nudge_strength = CONFIG.get("trading", {}).get("theme_nudge_strength", 0.20)
        theme_score = self.get_composite_score(ticker)
        nudge = theme_score * nudge_strength
        adjusted = base_confidence + (base_confidence * nudge)
        return max(0.0, min(1.0, adjusted))

    def classify_stocks(self, tickers: list[str]) -> dict[str, list[ThemeAlignment]]:
        """Uses Claude Code CLI to classify stocks against active themes.

        Sends the ticker list and theme definitions to Claude,
        which returns alignment scores and reasoning for each.
        """
        themes_json = json.dumps(
            [t.to_dict() for t in self.active_themes], indent=2
        )

        prompt = f"""You are a financial analyst. Classify each stock ticker against the following macro investment themes.

THEMES:
{themes_json}

TICKERS TO CLASSIFY:
{json.dumps(tickers)}

For each ticker, assess its alignment with each theme:
- Score from -1.0 (company works against this theme) to +1.0 (company strongly benefits from this theme)
- 0.0 means the theme is irrelevant to this company
- Only include non-zero alignments

Respond with ONLY valid JSON in this exact format, no other text:
{{
  "alignments": [
    {{
      "ticker": "AAPL",
      "theme_name": "ai_automation",
      "score": 0.7,
      "reasoning": "Major AI investment in Apple Intelligence"
    }}
  ]
}}"""

        max_budget = CONFIG.get("adaptation", {}).get("claude_max_budget_usd", 0.50)

        try:
            result = subprocess.run(
                [
                    "claude", "-p", prompt,
                    "--output-format", "text",
                    "--max-budget-usd", str(max_budget),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                return {}

            response_text = result.stdout.strip()
            data = json.loads(response_text)
            alignments = {}

            for item in data.get("alignments", []):
                ticker = item["ticker"]
                alignment = ThemeAlignment(
                    ticker=ticker,
                    theme_name=item["theme_name"],
                    score=max(-1.0, min(1.0, float(item["score"]))),
                    reasoning=item.get("reasoning", ""),
                )
                if ticker not in alignments:
                    alignments[ticker] = []
                alignments[ticker].append(alignment)

            self._alignments.update(alignments)
            return alignments

        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
            return {}

    def discover_themes(self, recent_headlines: list[str]) -> list[dict]:
        """Asks Claude to identify emerging macro themes from recent news.

        Returns a list of suggested themes with reasoning.
        These are suggestions only — not automatically added.
        """
        current_themes = json.dumps(
            [t.to_dict() for t in self.active_themes], indent=2
        )

        prompt = f"""You are a macro strategist. Review these recent news headlines and identify emerging macro investment themes.

CURRENT THEMES (already tracked):
{current_themes}

RECENT HEADLINES (past 7 days):
{json.dumps(recent_headlines[:200])}

Identify:
1. Any NEW macro themes not in the current list that appear to be emerging
2. Any current themes that seem to be fading or no longer relevant
3. Any current themes that should be updated

Respond with ONLY valid JSON:
{{
  "new_themes": [
    {{
      "name": "theme_name_snake_case",
      "description": "Why this is an emerging macro trend",
      "bullish_sectors": ["sector1", "sector2"],
      "bearish_sectors": ["sector3"],
      "confidence": 0.8
    }}
  ],
  "fading_themes": [
    {{
      "name": "existing_theme_name",
      "reason": "Why this theme is fading"
    }}
  ],
  "updated_themes": [
    {{
      "name": "existing_theme_name",
      "updates": "What should change and why"
    }}
  ]
}}"""

        max_budget = CONFIG.get("adaptation", {}).get("claude_max_budget_usd", 0.50)

        try:
            result = subprocess.run(
                [
                    "claude", "-p", prompt,
                    "--output-format", "text",
                    "--max-budget-usd", str(max_budget),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                return []

            data = json.loads(result.stdout.strip())
            return data.get("new_themes", [])

        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            return []
