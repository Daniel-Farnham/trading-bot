"""Tests for live universe."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.live.universe import LiveUniverse


@pytest.fixture
def universe(tmp_path):
    return LiveUniverse(path=tmp_path / "universe.json")


class TestAdd:
    def test_add_ticker(self, universe):
        assert universe.add("NVDA") is True
        assert universe.contains("NVDA")
        assert len(universe) == 1

    def test_add_duplicate_returns_false(self, universe):
        universe.add("NVDA")
        assert universe.add("NVDA") is False
        assert len(universe) == 1

    def test_add_normalizes_case(self, universe):
        universe.add("nvda")
        assert universe.contains("NVDA")

    def test_add_empty_returns_false(self, universe):
        assert universe.add("") is False

    def test_add_stores_metadata(self, universe):
        universe.add("AAPL", source="call1", reason="AI theme breakout")
        entries = universe.get_entries()
        assert entries[0]["ticker"] == "AAPL"
        assert entries[0]["source"] == "call1"
        assert entries[0]["reason"] == "AI theme breakout"

    def test_no_cap_on_universe(self, universe):
        """Universe has no max size — it grows freely."""
        for i in range(200):
            universe.add(f"TICK{i}")
        assert len(universe) == 200


class TestRemove:
    def test_remove_ticker(self, universe):
        universe.add("NVDA")
        universe.remove("NVDA")
        assert not universe.contains("NVDA")

    def test_remove_nonexistent_is_safe(self, universe):
        universe.remove("NVDA")


class TestSeedFromConfig:
    def test_seeds_from_config(self, tmp_path):
        mock_config = {
            "universe": {
                "ai_tech": ["NVDA", "MSFT", "GOOGL"],
                "healthcare": ["LLY", "NVO"],
            }
        }
        with patch("src.live.universe.CONFIG", mock_config):
            u = LiveUniverse(path=tmp_path / "universe.json")
            added = u.seed_from_config()

        assert added == 5
        assert u.contains("NVDA")
        assert u.contains("LLY")
        assert len(u) == 5

    def test_seed_skips_existing(self, tmp_path):
        mock_config = {
            "universe": {
                "ai_tech": ["NVDA", "MSFT"],
            }
        }
        with patch("src.live.universe.CONFIG", mock_config):
            u = LiveUniverse(path=tmp_path / "universe.json")
            u.add("NVDA", source="call1", reason="already here")
            added = u.seed_from_config()

        assert added == 1  # Only MSFT added
        assert len(u) == 2

    def test_seed_deduplicates_across_themes(self, tmp_path):
        mock_config = {
            "universe": {
                "ai_tech": ["NVDA", "MSFT"],
                "chips": ["NVDA", "TSM"],  # NVDA appears in both
            }
        }
        with patch("src.live.universe.CONFIG", mock_config):
            u = LiveUniverse(path=tmp_path / "universe.json")
            added = u.seed_from_config()

        assert added == 3  # NVDA, MSFT, TSM
        assert len(u) == 3

    def test_seed_empty_config(self, tmp_path):
        with patch("src.live.universe.CONFIG", {}):
            u = LiveUniverse(path=tmp_path / "universe.json")
            added = u.seed_from_config()

        assert added == 0


class TestPersistence:
    def test_survives_restart(self, tmp_path):
        path = tmp_path / "universe.json"
        u1 = LiveUniverse(path=path)
        u1.add("NVDA")
        u1.add("AAPL")

        u2 = LiveUniverse(path=path)
        assert u2.contains("NVDA")
        assert u2.contains("AAPL")
        assert len(u2) == 2

    def test_handles_corrupt_file(self, tmp_path):
        path = tmp_path / "universe.json"
        path.write_text("{broken json")
        u = LiveUniverse(path=path)
        assert len(u) == 0

    def test_handles_missing_file(self, tmp_path):
        u = LiveUniverse(path=tmp_path / "nonexistent.json")
        assert len(u) == 0


class TestGetTickers:
    def test_returns_ticker_list(self, universe):
        universe.add("NVDA")
        universe.add("AAPL")
        assert universe.get_tickers() == ["NVDA", "AAPL"]

    def test_empty_universe(self, universe):
        assert universe.get_tickers() == []
