from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.adaptation.journal import StrategyJournal


@pytest.fixture
def journal(tmp_path):
    return StrategyJournal(path=tmp_path / "test_journal.md", max_entries=5)


class TestJournalReadWrite:
    def test_empty_journal(self, journal):
        assert journal.read() == ""
        assert journal.get_entries() == []

    def test_get_recent_context_empty(self, journal):
        ctx = journal.get_recent_context()
        assert "first review" in ctx.lower()

    def test_append_entry(self, journal):
        journal.append_entry(
            date="2024-01-15",
            review_type="daily",
            portfolio_value=100500.0,
            total_return_pct=0.5,
            cash=85000.0,
            positions_count=2,
            trades_total=5,
            win_rate=60.0,
            changes=[
                {"param": "sentiment_buy_threshold", "old_value": 0.6, "new_value": 0.65, "reason": "Be more selective"}
            ],
            analysis="Win rate is decent but losses are too large.",
        )

        content = journal.read()
        assert "## Review — 2024-01-15 | Daily Tactical" in content
        assert "$100,500.00" in content
        assert "sentiment_buy_threshold" in content
        assert "Be more selective" in content
        assert "Win rate is decent" in content

    def test_append_multiple_entries(self, journal):
        for i in range(3):
            journal.append_entry(
                date=f"2024-01-{15 + i}",
                review_type="daily",
                portfolio_value=100000 + i * 100,
                total_return_pct=i * 0.1,
                cash=80000.0,
                positions_count=1,
                trades_total=3 + i,
                win_rate=50.0,
                changes=[],
            )

        entries = journal.get_entries()
        assert len(entries) == 3

    def test_weekly_review_label(self, journal):
        journal.append_entry(
            date="2024-01-20",
            review_type="weekly",
            portfolio_value=101000.0,
            total_return_pct=1.0,
            cash=80000.0,
            positions_count=3,
            trades_total=8,
            win_rate=62.5,
            changes=[],
            analysis="Themes performing well.",
        )

        content = journal.read()
        assert "Weekly Strategic" in content

    def test_no_changes_entry(self, journal):
        journal.append_entry(
            date="2024-01-15",
            review_type="daily",
            portfolio_value=100000.0,
            total_return_pct=0.0,
            cash=100000.0,
            positions_count=0,
            trades_total=3,
            win_rate=33.0,
            changes=[],
        )

        content = journal.read()
        assert "strategy unchanged" in content.lower()


class TestJournalTruncation:
    def test_truncates_to_max_entries(self, journal):
        # max_entries is 5
        for i in range(8):
            journal.append_entry(
                date=f"2024-01-{10 + i}",
                review_type="daily",
                portfolio_value=100000.0,
                total_return_pct=0.0,
                cash=80000.0,
                positions_count=0,
                trades_total=3,
                win_rate=50.0,
                changes=[],
            )

        entries = journal.get_entries()
        assert len(entries) == 5
        # Should keep the most recent entries
        assert "2024-01-17" in entries[-1]
        assert "2024-01-13" in entries[0]

    def test_no_truncation_under_limit(self, journal):
        for i in range(3):
            journal.append_entry(
                date=f"2024-01-{10 + i}",
                review_type="daily",
                portfolio_value=100000.0,
                total_return_pct=0.0,
                cash=80000.0,
                positions_count=0,
                trades_total=3,
                win_rate=50.0,
                changes=[],
            )

        entries = journal.get_entries()
        assert len(entries) == 3


class TestJournalContext:
    def test_get_recent_context(self, journal):
        for i in range(3):
            journal.append_entry(
                date=f"2024-01-{10 + i}",
                review_type="daily",
                portfolio_value=100000 + i * 500,
                total_return_pct=i * 0.5,
                cash=80000.0,
                positions_count=i,
                trades_total=3 + i,
                win_rate=50.0,
                changes=[],
                analysis=f"Review {i}",
            )

        ctx = journal.get_recent_context(max_entries=2)
        # Should contain the last 2 entries
        assert "2024-01-11" in ctx
        assert "2024-01-12" in ctx
        assert "2024-01-10" not in ctx

    def test_get_recent_context_all(self, journal):
        journal.append_entry(
            date="2024-01-10",
            review_type="daily",
            portfolio_value=100000.0,
            total_return_pct=0.0,
            cash=80000.0,
            positions_count=0,
            trades_total=3,
            win_rate=50.0,
            changes=[],
        )

        ctx = journal.get_recent_context()
        assert "2024-01-10" in ctx


class TestJournalClear:
    def test_clear(self, journal):
        journal.append_entry(
            date="2024-01-10",
            review_type="daily",
            portfolio_value=100000.0,
            total_return_pct=0.0,
            cash=80000.0,
            positions_count=0,
            trades_total=3,
            win_rate=50.0,
            changes=[],
        )

        assert journal.read() != ""
        journal.clear()
        assert journal.read() == ""

    def test_clear_nonexistent(self, journal):
        # Should not raise
        journal.clear()
