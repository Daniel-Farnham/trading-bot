"""Tests for live watchlist and universe."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from src.live.watchlist import LiveWatchlist, MAX_WATCHLIST, MAX_AGE_DAYS


@pytest.fixture
def watchlist(tmp_path):
    return LiveWatchlist(path=tmp_path / "watchlist.json")


class TestAdd:
    def test_add_ticker(self, watchlist):
        assert watchlist.add("NVDA") is True
        assert watchlist.contains("NVDA")
        assert len(watchlist) == 1

    def test_add_duplicate_returns_false(self, watchlist):
        watchlist.add("NVDA")
        assert watchlist.add("NVDA") is False
        assert len(watchlist) == 1

    def test_add_normalizes_case(self, watchlist):
        watchlist.add("nvda")
        assert watchlist.contains("NVDA")

    def test_add_empty_returns_false(self, watchlist):
        assert watchlist.add("") is False
        assert watchlist.add("  ") is False

    def test_add_stores_metadata(self, watchlist):
        watchlist.add("AAPL", source="call1", reason="AI theme")
        entries = watchlist.get_entries()
        assert entries[0]["ticker"] == "AAPL"
        assert entries[0]["source"] == "call1"
        assert entries[0]["reason"] == "AI theme"
        assert entries[0]["added_date"] == date.today().isoformat()

    def test_evicts_oldest_at_cap(self, tmp_path):
        wl = LiveWatchlist(path=tmp_path / "watchlist.json")
        for i in range(MAX_WATCHLIST):
            wl.add(f"TICK{i}")

        assert len(wl) == MAX_WATCHLIST
        assert wl.contains("TICK0")

        wl.add("NEW")
        assert len(wl) == MAX_WATCHLIST
        assert not wl.contains("TICK0")
        assert wl.contains("NEW")


class TestRemove:
    def test_remove_ticker(self, watchlist):
        watchlist.add("NVDA")
        watchlist.remove("NVDA")
        assert not watchlist.contains("NVDA")
        assert len(watchlist) == 0

    def test_remove_nonexistent_is_safe(self, watchlist):
        watchlist.remove("NVDA")  # No error

    def test_remove_normalizes_case(self, watchlist):
        watchlist.add("NVDA")
        watchlist.remove("nvda")
        assert not watchlist.contains("NVDA")


class TestPrune:
    def test_prunes_old_tickers(self, tmp_path):
        wl = LiveWatchlist(path=tmp_path / "watchlist.json")
        old_date = (date.today() - timedelta(days=MAX_AGE_DAYS + 1)).isoformat()
        wl._entries = [
            {"ticker": "OLD", "added_date": old_date, "source": "call1", "reason": ""},
            {"ticker": "NEW", "added_date": date.today().isoformat(), "source": "call1", "reason": ""},
        ]
        wl._save()

        pruned = wl.prune()
        assert pruned == ["OLD"]
        assert not wl.contains("OLD")
        assert wl.contains("NEW")

    def test_prune_empty_watchlist(self, watchlist):
        pruned = watchlist.prune()
        assert pruned == []

    def test_prune_nothing_expired(self, watchlist):
        watchlist.add("NVDA")
        pruned = watchlist.prune()
        assert pruned == []
        assert watchlist.contains("NVDA")


class TestPersistence:
    def test_survives_restart(self, tmp_path):
        path = tmp_path / "watchlist.json"
        wl1 = LiveWatchlist(path=path)
        wl1.add("NVDA")
        wl1.add("AAPL")

        wl2 = LiveWatchlist(path=path)
        assert wl2.contains("NVDA")
        assert wl2.contains("AAPL")
        assert len(wl2) == 2

    def test_handles_corrupt_file(self, tmp_path):
        path = tmp_path / "watchlist.json"
        path.write_text("not valid json{{{")
        wl = LiveWatchlist(path=path)
        assert len(wl) == 0

    def test_handles_missing_file(self, tmp_path):
        wl = LiveWatchlist(path=tmp_path / "nonexistent.json")
        assert len(wl) == 0


class TestGetTickers:
    def test_returns_ticker_list(self, watchlist):
        watchlist.add("NVDA")
        watchlist.add("AAPL")
        tickers = watchlist.get_tickers()
        assert tickers == ["NVDA", "AAPL"]

    def test_empty_watchlist(self, watchlist):
        assert watchlist.get_tickers() == []
