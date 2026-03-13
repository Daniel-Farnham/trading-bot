from __future__ import annotations

import pytest

from src.strategy.thesis_manager import ThesisManager


@pytest.fixture
def manager(tmp_path):
    """ThesisManager with all files rooted in tmp_path."""
    # Override config paths by passing base_dir and patching CONFIG
    mgr = ThesisManager.__new__(ThesisManager)
    from pathlib import Path
    mgr._paths = {
        "theses": tmp_path / "active_theses.md",
        "ledger": tmp_path / "portfolio_ledger.md",
        "summaries": tmp_path / "quarterly_summaries.md",
        "lessons": tmp_path / "lessons_learned.md",
        "sim_log": tmp_path / "simulation_log.md",
    }
    mgr._max_theses = 15
    mgr._max_summaries = 8
    return mgr


@pytest.fixture
def small_manager(tmp_path):
    """ThesisManager with low limits for truncation tests."""
    mgr = ThesisManager.__new__(ThesisManager)
    from pathlib import Path
    mgr._paths = {
        "theses": tmp_path / "active_theses.md",
        "ledger": tmp_path / "portfolio_ledger.md",
        "summaries": tmp_path / "quarterly_summaries.md",
        "lessons": tmp_path / "lessons_learned.md",
        "sim_log": tmp_path / "simulation_log.md",
    }
    mgr._max_theses = 3
    mgr._max_summaries = 2
    return mgr


class TestActiveTheses:
    def test_empty(self, manager):
        assert manager.get_all_theses() == []
        assert manager.get_by_ticker("AAPL") is None

    def test_add_thesis(self, manager):
        result = manager.add_thesis(
            ticker="NVDA", direction="LONG",
            thesis="AI chip demand growing",
            entry_price=800.0, target_price=1000.0, stop_price=700.0,
            timeframe="3-6 months", confidence="high",
        )
        assert result is True
        theses = manager.get_all_theses()
        assert len(theses) == 1
        assert theses[0]["ticker"] == "NVDA"
        assert theses[0]["direction"] == "LONG"
        assert theses[0]["thesis"] == "AI chip demand growing"
        assert theses[0]["entry_price"] == 800.0
        assert theses[0]["target_price"] == 1000.0
        assert theses[0]["confidence"] == "high"

    def test_add_multiple(self, manager):
        for ticker in ["AAPL", "MSFT", "GOOGL"]:
            manager.add_thesis(
                ticker=ticker, direction="LONG", thesis=f"Thesis for {ticker}",
                entry_price=100.0, target_price=150.0, stop_price=80.0,
            )
        assert len(manager.get_all_theses()) == 3

    def test_get_by_ticker(self, manager):
        manager.add_thesis(
            ticker="AVGO", direction="LONG", thesis="AI networking",
            entry_price=150.0, target_price=200.0, stop_price=120.0,
        )
        result = manager.get_by_ticker("AVGO")
        assert result is not None
        assert result["ticker"] == "AVGO"

    def test_get_by_ticker_case_insensitive(self, manager):
        manager.add_thesis(
            ticker="AVGO", direction="LONG", thesis="test",
            entry_price=100.0, target_price=150.0, stop_price=80.0,
        )
        assert manager.get_by_ticker("avgo") is not None

    def test_update_thesis(self, manager):
        manager.add_thesis(
            ticker="TSLA", direction="LONG", thesis="EV growth",
            entry_price=200.0, target_price=300.0, stop_price=150.0,
            confidence="medium",
        )
        result = manager.update_thesis("TSLA", confidence="low", thesis="EV slowdown")
        assert result is True
        t = manager.get_by_ticker("TSLA")
        assert t["confidence"] == "low"
        assert t["thesis"] == "EV slowdown"

    def test_update_nonexistent(self, manager):
        assert manager.update_thesis("FAKE", confidence="high") is False

    def test_remove_thesis(self, manager):
        manager.add_thesis(
            ticker="META", direction="LONG", thesis="Metaverse",
            entry_price=300.0, target_price=400.0, stop_price=250.0,
        )
        assert manager.remove_thesis("META") is True
        assert manager.get_all_theses() == []

    def test_remove_nonexistent(self, manager):
        assert manager.remove_thesis("FAKE") is False

    def test_max_theses_limit(self, small_manager):
        for i in range(3):
            small_manager.add_thesis(
                ticker=f"T{i}", direction="LONG", thesis=f"Thesis {i}",
                entry_price=100.0, target_price=150.0, stop_price=80.0,
            )
        assert len(small_manager.get_all_theses()) == 3

        # 4th should fail
        result = small_manager.add_thesis(
            ticker="T3", direction="LONG", thesis="Too many",
            entry_price=100.0, target_price=150.0, stop_price=80.0,
        )
        assert result is False
        assert len(small_manager.get_all_theses()) == 3

    def test_add_existing_ticker_updates(self, manager):
        manager.add_thesis(
            ticker="AAPL", direction="LONG", thesis="Original",
            entry_price=150.0, target_price=200.0, stop_price=120.0,
        )
        manager.add_thesis(
            ticker="AAPL", direction="LONG", thesis="Updated thesis",
            entry_price=160.0, target_price=210.0, stop_price=130.0,
        )
        theses = manager.get_all_theses()
        assert len(theses) == 1
        assert theses[0]["thesis"] == "Updated thesis"
        assert theses[0]["entry_price"] == 160.0


class TestPortfolioLedger:
    def test_empty(self, manager):
        assert manager.get_holdings() == []

    def test_update_position(self, manager):
        manager.update_position(
            ticker="NVDA", side="LONG", qty=10,
            entry_price=800.0, current_value=8500.0, date_opened="2024-01-15",
        )
        holdings = manager.get_holdings()
        assert len(holdings) == 1
        assert holdings[0]["ticker"] == "NVDA"
        assert holdings[0]["side"] == "LONG"
        assert holdings[0]["qty"] == 10.0
        assert holdings[0]["entry_price"] == 800.0

    def test_update_existing_position(self, manager):
        manager.update_position("AAPL", "LONG", 5, 150.0, 800.0, "2024-01-10")
        manager.update_position("AAPL", "LONG", 10, 155.0, 1600.0, "2024-01-10")
        holdings = manager.get_holdings()
        assert len(holdings) == 1
        assert holdings[0]["qty"] == 10.0
        assert holdings[0]["entry_price"] == 155.0

    def test_remove_position(self, manager):
        manager.update_position("MSFT", "LONG", 5, 400.0, 2100.0, "2024-01-12")
        assert manager.remove_position("MSFT") is True
        assert manager.get_holdings() == []

    def test_remove_nonexistent(self, manager):
        assert manager.remove_position("FAKE") is False

    def test_update_values(self, manager):
        manager.update_position("AAPL", "LONG", 10, 150.0, 1500.0, "2024-01-10")
        manager.update_position("MSFT", "LONG", 5, 400.0, 2000.0, "2024-01-11")
        manager.update_values({"AAPL": 1600.0, "MSFT": 2100.0})
        holdings = manager.get_holdings()
        by_ticker = {h["ticker"]: h for h in holdings}
        assert by_ticker["AAPL"]["current_value"] == 1600.0
        assert by_ticker["MSFT"]["current_value"] == 2100.0

    def test_multiple_positions(self, manager):
        manager.update_position("AAPL", "LONG", 10, 150.0, 1500.0, "2024-01-10")
        manager.update_position("TSLA", "SHORT", 3, 200.0, 600.0, "2024-01-12")
        holdings = manager.get_holdings()
        assert len(holdings) == 2
        sides = {h["ticker"]: h["side"] for h in holdings}
        assert sides["AAPL"] == "LONG"
        assert sides["TSLA"] == "SHORT"


class TestQuarterlySummaries:
    def test_empty(self, manager):
        assert manager.get_recent_summaries() == []

    def test_append_summary(self, manager):
        manager.append_summary("Q1", 2024, "Started with $100k. Focused on AI stocks.")
        summaries = manager.get_recent_summaries()
        assert len(summaries) == 1
        assert "Q1 2024" in summaries[0]
        assert "AI stocks" in summaries[0]

    def test_multiple_summaries(self, manager):
        manager.append_summary("Q1", 2024, "Q1 body")
        manager.append_summary("Q2", 2024, "Q2 body")
        manager.append_summary("Q3", 2024, "Q3 body")
        summaries = manager.get_recent_summaries()
        assert len(summaries) == 3

    def test_truncation(self, small_manager):
        for i in range(1, 5):
            small_manager.append_summary(f"Q{i}", 2024, f"Quarter {i} body")
        summaries = small_manager.get_recent_summaries()
        assert len(summaries) == 2
        # Should keep most recent
        assert "Q4 2024" in summaries[-1]
        assert "Q3 2024" in summaries[-2]


class TestLessonsLearned:
    def test_empty(self, manager):
        assert manager.get_all_lessons() == []

    def test_append_lesson(self, manager):
        manager.append_lesson("Never hold through earnings without a hedge.")
        lessons = manager.get_all_lessons()
        assert len(lessons) == 1
        assert "earnings" in lessons[0].lower()

    def test_multiple_lessons(self, manager):
        manager.append_lesson("Lesson one")
        manager.append_lesson("Lesson two")
        manager.append_lesson("Lesson three")
        lessons = manager.get_all_lessons()
        assert len(lessons) == 3
        assert "Lesson 1" in lessons[0]
        assert "Lesson 3" in lessons[2]


class TestSimulationLog:
    def test_empty(self, manager):
        assert manager.get_all_sim_runs() == []

    def test_append_run(self, manager):
        manager.append_sim_run("2024-01-15_001", "Return: +5.2%, Sharpe: 1.3")
        runs = manager.get_all_sim_runs()
        assert len(runs) == 1
        assert "2024-01-15_001" in runs[0]

    def test_multiple_runs(self, manager):
        manager.append_sim_run("run_1", "Result 1")
        manager.append_sim_run("run_2", "Result 2")
        assert len(manager.get_all_sim_runs()) == 2


class TestDecisionContext:
    def test_empty_context(self, manager):
        ctx = manager.get_decision_context()
        assert "Active Theses" in ctx
        assert "Portfolio Ledger" in ctx
        assert "Quarterly Summaries" in ctx
        assert "Lessons Learned" in ctx
        assert "No active theses" in ctx

    def test_excludes_sim_log(self, manager):
        manager.append_sim_run("run_1", "Some sim result")
        ctx = manager.get_decision_context()
        assert "sim result" not in ctx.lower()
        assert "run_1" not in ctx

    def test_includes_all_four_files(self, manager):
        manager.add_thesis(
            ticker="NVDA", direction="LONG", thesis="AI demand",
            entry_price=800.0, target_price=1000.0, stop_price=700.0,
        )
        manager.update_position("NVDA", "LONG", 10, 800.0, 8500.0, "2024-01-15")
        manager.append_summary("Q1", 2024, "Good quarter")
        manager.append_lesson("Cut losers fast")

        ctx = manager.get_decision_context()
        assert "NVDA" in ctx
        assert "AI demand" in ctx
        assert "$800.00" in ctx
        assert "Good quarter" in ctx
        assert "Cut losers fast" in ctx


class TestClearAll:
    def test_clear_all(self, manager):
        manager.add_thesis(
            ticker="AAPL", direction="LONG", thesis="test",
            entry_price=100.0, target_price=150.0, stop_price=80.0,
        )
        manager.update_position("AAPL", "LONG", 5, 100.0, 500.0, "2024-01-10")
        manager.append_lesson("test lesson")
        manager.append_sim_run("run_1", "test run")

        manager.clear_all()

        assert manager.get_all_theses() == []
        assert manager.get_holdings() == []
        assert manager.get_all_lessons() == []
        assert manager.get_all_sim_runs() == []
