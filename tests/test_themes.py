from __future__ import annotations

from unittest.mock import patch, MagicMock
import json

import pytest

from src.strategy.themes import (
    Theme,
    ThemeAlignment,
    ThemeManager,
    DEFAULT_THEMES,
)


def _make_manager_with_alignments() -> ThemeManager:
    """Creates a ThemeManager with pre-populated alignment data."""
    manager = ThemeManager()
    manager._alignments = {
        "ENPH": [
            ThemeAlignment("ENPH", "climate_transition", 0.9, "Solar energy company"),
            ThemeAlignment("ENPH", "ai_automation", 0.1, "Minor AI use"),
        ],
        "XOM": [
            ThemeAlignment("XOM", "climate_transition", -0.8, "Oil major"),
        ],
        "AAPL": [],
    }
    return manager


class TestTheme:
    def test_create_theme(self):
        theme = Theme(
            name="test_theme",
            description="A test theme",
            bullish_sectors=["tech"],
            bearish_sectors=["old_tech"],
        )
        assert theme.name == "test_theme"
        assert theme.active is True
        assert theme.discovered_at is not None

    def test_theme_to_dict(self):
        theme = Theme(name="test", description="Test", bullish_sectors=["a"])
        d = theme.to_dict()
        assert d["name"] == "test"
        assert d["active"] is True
        assert "bullish_sectors" in d


class TestThemeManager:
    def test_default_themes_loaded(self):
        manager = ThemeManager()
        assert len(manager.active_themes) == len(DEFAULT_THEMES)

    def test_custom_themes(self):
        themes = [Theme(name="custom", description="Custom theme")]
        manager = ThemeManager(themes=themes)
        assert len(manager.active_themes) == 1
        assert manager.active_themes[0].name == "custom"

    def test_add_theme(self):
        manager = ThemeManager(themes=[])
        manager.add_theme(Theme(name="new", description="New theme"))
        assert len(manager.active_themes) == 1

    def test_add_duplicate_theme_ignored(self):
        manager = ThemeManager(themes=[Theme(name="a", description="A")])
        manager.add_theme(Theme(name="a", description="A duplicate"))
        assert len(manager.active_themes) == 1

    def test_remove_theme(self):
        manager = ThemeManager(themes=[
            Theme(name="a", description="A"),
            Theme(name="b", description="B"),
        ])
        manager.remove_theme("a")
        assert len(manager.active_themes) == 1
        assert manager.active_themes[0].name == "b"

    def test_deactivate_theme(self):
        manager = ThemeManager(themes=[
            Theme(name="a", description="A"),
            Theme(name="b", description="B"),
        ])
        manager.deactivate_theme("a")
        assert len(manager.active_themes) == 1
        assert manager.active_themes[0].name == "b"

    def test_get_alignments_exists(self):
        manager = _make_manager_with_alignments()
        alignments = manager.get_alignments("ENPH")
        assert len(alignments) == 2

    def test_get_alignments_empty(self):
        manager = _make_manager_with_alignments()
        alignments = manager.get_alignments("UNKNOWN")
        assert alignments == []

    def test_composite_score_positive(self):
        manager = _make_manager_with_alignments()
        score = manager.get_composite_score("ENPH")
        assert score == pytest.approx(0.5, abs=0.01)  # (0.9 + 0.1) / 2

    def test_composite_score_negative(self):
        manager = _make_manager_with_alignments()
        score = manager.get_composite_score("XOM")
        assert score == pytest.approx(-0.8, abs=0.01)

    def test_composite_score_no_data(self):
        manager = _make_manager_with_alignments()
        score = manager.get_composite_score("UNKNOWN")
        assert score == 0.0


class TestThemeNudge:
    def test_positive_nudge_boosts_confidence(self):
        manager = _make_manager_with_alignments()
        # ENPH has composite score ~0.5
        adjusted = manager.apply_theme_nudge("ENPH", 0.7)
        assert adjusted > 0.7

    def test_negative_nudge_reduces_confidence(self):
        manager = _make_manager_with_alignments()
        # XOM has composite score -0.8
        adjusted = manager.apply_theme_nudge("XOM", 0.7)
        assert adjusted < 0.7

    def test_no_alignment_no_change(self):
        manager = _make_manager_with_alignments()
        adjusted = manager.apply_theme_nudge("UNKNOWN", 0.7)
        assert adjusted == 0.7

    def test_nudge_clamped_to_max_1(self):
        manager = _make_manager_with_alignments()
        adjusted = manager.apply_theme_nudge("ENPH", 0.99)
        assert adjusted <= 1.0

    def test_nudge_clamped_to_min_0(self):
        manager = _make_manager_with_alignments()
        adjusted = manager.apply_theme_nudge("XOM", 0.05)
        assert adjusted >= 0.0


class TestClaudeClassification:
    @patch("src.strategy.themes.subprocess.run")
    def test_classify_stocks_success(self, mock_run):
        response = json.dumps({
            "alignments": [
                {
                    "ticker": "TSLA",
                    "theme_name": "climate_transition",
                    "score": 0.85,
                    "reasoning": "EV leader",
                },
                {
                    "ticker": "TSLA",
                    "theme_name": "ai_automation",
                    "score": 0.6,
                    "reasoning": "Autonomous driving",
                },
            ]
        })
        mock_run.return_value = MagicMock(
            returncode=0, stdout=response
        )

        manager = ThemeManager()
        result = manager.classify_stocks(["TSLA"])

        assert "TSLA" in result
        assert len(result["TSLA"]) == 2
        assert result["TSLA"][0].score == 0.85

    @patch("src.strategy.themes.subprocess.run")
    def test_classify_stocks_clamps_scores(self, mock_run):
        response = json.dumps({
            "alignments": [
                {"ticker": "X", "theme_name": "t", "score": 5.0, "reasoning": ""},
            ]
        })
        mock_run.return_value = MagicMock(returncode=0, stdout=response)

        manager = ThemeManager()
        result = manager.classify_stocks(["X"])
        assert result["X"][0].score == 1.0  # Clamped

    @patch("src.strategy.themes.subprocess.run")
    def test_classify_stocks_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        manager = ThemeManager()
        result = manager.classify_stocks(["AAPL"])
        assert result == {}

    @patch("src.strategy.themes.subprocess.run")
    def test_classify_stocks_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("claude", 120)

        manager = ThemeManager()
        result = manager.classify_stocks(["AAPL"])
        assert result == {}

    @patch("src.strategy.themes.subprocess.run")
    def test_classify_stocks_bad_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")

        manager = ThemeManager()
        result = manager.classify_stocks(["AAPL"])
        assert result == {}


class TestThemeDiscovery:
    @patch("src.strategy.themes.subprocess.run")
    def test_discover_themes_success(self, mock_run):
        response = json.dumps({
            "new_themes": [
                {
                    "name": "deglobalization",
                    "description": "Reshoring of supply chains",
                    "bullish_sectors": ["domestic manufacturing"],
                    "bearish_sectors": ["shipping"],
                    "confidence": 0.75,
                }
            ],
            "fading_themes": [],
            "updated_themes": [],
        })
        mock_run.return_value = MagicMock(returncode=0, stdout=response)

        manager = ThemeManager()
        suggestions = manager.discover_themes(["Headline 1", "Headline 2"])

        assert len(suggestions) == 1
        assert suggestions[0]["name"] == "deglobalization"

    @patch("src.strategy.themes.subprocess.run")
    def test_discover_themes_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        manager = ThemeManager()
        suggestions = manager.discover_themes(["Headline"])
        assert suggestions == []
