from __future__ import annotations

import pytest

from src.strategy.thesis_manager import ThesisManager


@pytest.fixture
def manager(tmp_path):
    """ThesisManager with all files rooted in tmp_path."""
    mgr = ThesisManager.__new__(ThesisManager)
    mgr._paths = {
        "theses": tmp_path / "active_theses.md",
        "ledger": tmp_path / "portfolio_ledger.md",
        "summaries": tmp_path / "quarterly_summaries.md",
        "lessons": tmp_path / "lessons_learned.md",
        "themes": tmp_path / "themes.md",
        "beliefs": tmp_path / "beliefs.md",
        "world_view": tmp_path / "world_view.md",
        "tactical_view": tmp_path / "tactical_view.md",
        "journal": tmp_path / "decision_journal.md",
    }
    mgr._max_theses = 15
    mgr._max_watching = 5
    mgr._watching_expiry_reviews = 6
    mgr._watching = []
    mgr._max_summaries = 8
    mgr._max_themes = 8
    mgr._max_lessons = 15
    mgr._max_beliefs = 5
    mgr._max_journal_entries = 12
    return mgr


@pytest.fixture
def small_manager(tmp_path):
    """ThesisManager with low limits for truncation tests."""
    mgr = ThesisManager.__new__(ThesisManager)
    mgr._paths = {
        "theses": tmp_path / "active_theses.md",
        "ledger": tmp_path / "portfolio_ledger.md",
        "summaries": tmp_path / "quarterly_summaries.md",
        "lessons": tmp_path / "lessons_learned.md",
        "themes": tmp_path / "themes.md",
        "beliefs": tmp_path / "beliefs.md",
        "world_view": tmp_path / "world_view.md",
        "tactical_view": tmp_path / "tactical_view.md",
        "journal": tmp_path / "decision_journal.md",
    }
    mgr._max_theses = 3
    mgr._max_watching = 2
    mgr._watching_expiry_reviews = 6
    mgr._watching = []
    mgr._max_summaries = 2
    mgr._max_themes = 3
    mgr._max_lessons = 3
    mgr._max_beliefs = 2
    mgr._max_journal_entries = 12
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
        assert lessons[0]["score"] == 1
        assert lessons[0]["number"] == 1
        assert "earnings" in lessons[0]["content"].lower()

    def test_multiple_lessons(self, manager):
        manager.append_lesson("Lesson one")
        manager.append_lesson("Lesson two")
        manager.append_lesson("Lesson three")
        lessons = manager.get_all_lessons()
        assert len(lessons) == 3
        assert lessons[0]["number"] == 1
        assert lessons[2]["number"] == 3

    def test_lesson_score_starts_at_1(self, manager):
        manager.append_lesson("Test lesson")
        lessons = manager.get_all_lessons()
        assert lessons[0]["score"] == 1

    def test_increment_lesson_score(self, manager):
        manager.append_lesson("Test lesson")
        manager.increment_lesson_score(1)
        lessons = manager.get_all_lessons()
        assert lessons[0]["score"] == 2

    def test_increment_capped_at_5(self, manager):
        manager.append_lesson("Test lesson")
        for _ in range(10):
            manager.increment_lesson_score(1)
        lessons = manager.get_all_lessons()
        assert lessons[0]["score"] == 5

    def test_decrement_lesson_score(self, manager):
        manager.append_lesson("Test lesson")
        manager.increment_lesson_score(1)  # score -> 2
        manager.decrement_lesson_score(1)  # score -> 1
        lessons = manager.get_all_lessons()
        assert lessons[0]["score"] == 1

    def test_decrement_removes_at_zero(self, manager):
        manager.append_lesson("Test lesson")  # score 1
        manager.decrement_lesson_score(1)  # score -> 0, auto-remove
        lessons = manager.get_all_lessons()
        assert len(lessons) == 0

    def test_remove_lesson_renumbers(self, manager):
        manager.append_lesson("Lesson A")
        manager.append_lesson("Lesson B")
        manager.append_lesson("Lesson C")
        manager.remove_lesson(2)
        lessons = manager.get_all_lessons()
        assert len(lessons) == 2
        assert lessons[0]["number"] == 1
        assert lessons[1]["number"] == 2
        assert "Lesson A" in lessons[0]["content"]
        assert "Lesson C" in lessons[1]["content"]

    def test_max_lessons_evicts_lowest_score(self, small_manager):
        """When at max, new lesson evicts the lowest-scored one."""
        small_manager.append_lesson("Lesson A")  # score 1
        small_manager.append_lesson("Lesson B")  # score 1
        small_manager.append_lesson("Lesson C")  # score 1
        # Boost B's score so it survives
        small_manager.increment_lesson_score(2)  # B -> score 2
        small_manager.increment_lesson_score(3)  # C -> score 2

        # Add a 4th — should evict the lowest (A at score 1)
        small_manager.append_lesson("Lesson D")
        lessons = small_manager.get_all_lessons()
        assert len(lessons) == 3
        contents = [l["content"] for l in lessons]
        assert any("Lesson B" in c for c in contents)
        assert any("Lesson D" in c for c in contents)

    def test_backward_compat_old_format(self, manager):
        """Old format (no score bracket) parsed as score 3."""
        # Write old format directly
        old_content = "## Lesson 1\nOld lesson content\n\n---\n\n## Lesson 2\nAnother old lesson\n\n---\n"
        manager._write("lessons", old_content)
        lessons = manager.get_all_lessons()
        assert len(lessons) == 2
        assert lessons[0]["score"] == 3
        assert lessons[1]["score"] == 3
        assert "Old lesson content" in lessons[0]["content"]

    def test_increment_nonexistent(self, manager):
        assert manager.increment_lesson_score(99) is False

    def test_decrement_nonexistent(self, manager):
        assert manager.decrement_lesson_score(99) is False

    def test_remove_nonexistent(self, manager):
        assert manager.remove_lesson(99) is False


class TestBeliefs:
    def test_empty(self, manager):
        assert manager.get_all_beliefs() == []

    def test_add_belief(self, manager):
        result = manager.add_belief("Never catch falling knives", "Wait for trend reversal before buying dips", [1, 3])
        assert result is True
        beliefs = manager.get_all_beliefs()
        assert len(beliefs) == 1
        assert beliefs[0]["name"] == "Never catch falling knives"
        assert beliefs[0]["description"] == "Wait for trend reversal before buying dips"
        assert beliefs[0]["supporting_lessons"] == [1, 3]
        assert beliefs[0]["score"] == 3

    def test_add_existing_updates(self, manager):
        manager.add_belief("Test Belief", "Original", [1])
        manager.add_belief("Test Belief", "Updated", [1, 2])
        beliefs = manager.get_all_beliefs()
        assert len(beliefs) == 1
        assert beliefs[0]["description"] == "Updated"
        assert beliefs[0]["supporting_lessons"] == [1, 2]

    def test_max_beliefs_limit(self, small_manager):
        small_manager.add_belief("Belief 1", "Desc 1")
        small_manager.add_belief("Belief 2", "Desc 2")
        result = small_manager.add_belief("Belief 3", "Too many")
        assert result is False
        assert len(small_manager.get_all_beliefs()) == 2

    def test_update_belief(self, manager):
        manager.add_belief("Test", "Original desc", [1])
        result = manager.update_belief("Test", description="New desc", supporting_lessons=[1, 2, 3])
        assert result is True
        beliefs = manager.get_all_beliefs()
        assert beliefs[0]["description"] == "New desc"
        assert beliefs[0]["supporting_lessons"] == [1, 2, 3]

    def test_update_nonexistent(self, manager):
        assert manager.update_belief("Fake", description="test") is False

    def test_remove_belief(self, manager):
        manager.add_belief("Test", "Desc")
        assert manager.remove_belief("Test") is True
        assert manager.get_all_beliefs() == []

    def test_remove_nonexistent(self, manager):
        assert manager.remove_belief("Fake") is False

    def test_beliefs_in_decision_context(self, manager):
        manager.add_belief("Trend Following", "Always trade with the trend", [1, 3])
        ctx = manager.get_decision_context()
        assert "Trend Following" in ctx
        assert "Investment Beliefs" in ctx

    def test_empty_beliefs_in_decision_context(self, manager):
        ctx = manager.get_decision_context()
        assert "No beliefs established yet" in ctx


class TestDecisionContext:
    def test_empty_context(self, manager):
        ctx = manager.get_decision_context()
        assert "Active Theses" in ctx
        assert "Portfolio Ledger" in ctx
        assert "Quarterly Summaries" in ctx
        assert "Lessons Learned" in ctx
        assert "No active theses" in ctx

    def test_includes_all_memory_files(self, manager):
        manager.add_thesis(
            ticker="NVDA", direction="LONG", thesis="AI demand",
            entry_price=800.0, target_price=1000.0, stop_price=700.0,
        )
        manager.update_position("NVDA", "LONG", 10, 800.0, 8500.0, "2024-01-15")
        manager.append_summary("Q1", 2024, "Good quarter")
        manager.append_lesson("Cut losers fast")
        manager.add_belief("Trend Following", "Always follow the trend", [1])

        ctx = manager.get_decision_context()
        assert "NVDA" in ctx
        assert "AI demand" in ctx
        assert "$800.00" in ctx
        assert "Good quarter" in ctx
        assert "Cut losers fast" in ctx
        assert "Trend Following" in ctx

    def test_beliefs_appear_above_lessons(self, manager):
        manager.add_belief("My Belief", "A principle", [1])
        manager.append_lesson("A lesson")
        ctx = manager.get_decision_context()
        belief_pos = ctx.index("Investment Beliefs")
        lesson_pos = ctx.index("Lessons Learned")
        assert belief_pos < lesson_pos

    def test_include_ledger_false_omits_ledger_section(self, manager):
        """Live path: ledger section should be excluded; Alpaca surfaces positions."""
        manager.add_thesis(
            ticker="NVDA", direction="LONG", thesis="AI demand",
            entry_price=800.0, target_price=1000.0, stop_price=700.0,
        )
        manager.update_position("NVDA", "LONG", 10, 800.0, 8500.0, "2024-01-15")

        ctx_live = manager.get_decision_context(include_ledger=False)
        # Narrative still present
        assert "AI demand" in ctx_live
        assert "Active Theses" in ctx_live
        # Ledger section header explicitly absent
        assert "Portfolio Ledger" not in ctx_live
        # And the ledger table format markers shouldn't leak in
        assert "Date Opened" not in ctx_live

    def test_include_ledger_true_keeps_ledger_section(self, manager):
        """Sim path (default): ledger remains for backward compatibility."""
        manager.update_position("NVDA", "LONG", 10, 800.0, 8500.0, "2024-01-15")
        ctx_sim = manager.get_decision_context(include_ledger=True)
        assert "Portfolio Ledger" in ctx_sim
        assert "NVDA" in ctx_sim


class TestThemes:
    def test_empty(self, manager):
        assert manager.get_all_themes() == []

    def test_add_theme(self, manager):
        result = manager.add_theme("AI/Automation", "Companies building AI", score=3)
        assert result is True
        themes = manager.get_all_themes()
        assert len(themes) == 1
        assert themes[0]["name"] == "AI/Automation"
        assert themes[0]["score"] == 3
        assert themes[0]["description"] == "Companies building AI"

    def test_get_theme(self, manager):
        manager.add_theme("AI/Automation", "Companies building AI")
        t = manager.get_theme("AI/Automation")
        assert t is not None
        assert t["name"] == "AI/Automation"

    def test_get_theme_case_insensitive(self, manager):
        manager.add_theme("AI/Automation", "Companies building AI")
        assert manager.get_theme("ai/automation") is not None

    def test_add_multiple(self, manager):
        manager.add_theme("AI", "Artificial intelligence")
        manager.add_theme("Climate", "Clean energy transition")
        manager.add_theme("Healthcare", "Aging populations")
        assert len(manager.get_all_themes()) == 3

    def test_add_existing_updates(self, manager):
        manager.add_theme("AI", "Original description", score=3)
        manager.add_theme("AI", "Updated description", score=4)
        themes = manager.get_all_themes()
        assert len(themes) == 1
        assert themes[0]["description"] == "Updated description"
        assert themes[0]["score"] == 4

    def test_max_themes_limit(self, small_manager):
        for i in range(3):
            small_manager.add_theme(f"Theme {i}", f"Description {i}")
        assert len(small_manager.get_all_themes()) == 3
        result = small_manager.add_theme("Theme 3", "Too many")
        assert result is False
        assert len(small_manager.get_all_themes()) == 3

    def test_update_score_up(self, manager):
        manager.add_theme("AI", "Artificial intelligence", score=3)
        manager.update_theme_score("AI", +1)
        t = manager.get_theme("AI")
        assert t["score"] == 4

    def test_update_score_down(self, manager):
        manager.add_theme("AI", "Artificial intelligence", score=3)
        manager.update_theme_score("AI", -1)
        t = manager.get_theme("AI")
        assert t["score"] == 2

    def test_score_clamped_at_5(self, manager):
        manager.add_theme("AI", "Artificial intelligence", score=5)
        manager.update_theme_score("AI", +1)
        t = manager.get_theme("AI")
        assert t["score"] == 5

    def test_score_1_survives(self, manager):
        manager.add_theme("Weak", "Fading theme", score=2)
        manager.update_theme_score("Weak", -1)
        # Score hit 1 — should survive (only removed below 1)
        assert manager.get_theme("Weak") is not None
        assert manager.get_theme("Weak")["score"] == 1

    def test_score_below_1_auto_removes(self, manager):
        manager.add_theme("Dying", "Dead theme", score=1)
        manager.update_theme_score("Dying", -1)
        # Score dropped to 0 — should be auto-removed
        assert manager.get_theme("Dying") is None
        assert len(manager.get_all_themes()) == 0

    def test_update_nonexistent(self, manager):
        assert manager.update_theme_score("Fake", +1) is False

    def test_remove_theme(self, manager):
        manager.add_theme("AI", "Artificial intelligence")
        assert manager.remove_theme("AI") is True
        assert manager.get_all_themes() == []

    def test_remove_nonexistent(self, manager):
        assert manager.remove_theme("Fake") is False

    def test_themes_in_decision_context(self, manager):
        manager.add_theme("AI/Automation", "Companies building AI", score=4)
        ctx = manager.get_decision_context()
        assert "AI/Automation" in ctx
        assert "[4/5]" in ctx

    def test_empty_themes_in_decision_context(self, manager):
        ctx = manager.get_decision_context()
        assert "No themes set" in ctx


class TestTacticalLog:
    """append_tactical_observation: rolling daily-observation log capped at 14."""

    def test_appends_first_entry(self, manager):
        manager.append_tactical_observation("2026-04-01", "Iran tensions intensifying")
        tv = manager.get_tactical_view()
        assert "- 2026-04-01: Iran tensions intensifying" in tv
        # Single header, no duplication
        assert tv.count("# Tactical View") == 1

    def test_appends_additional_entries_in_order(self, manager):
        manager.append_tactical_observation("2026-04-01", "first")
        manager.append_tactical_observation("2026-04-02", "second")
        manager.append_tactical_observation("2026-04-03", "third")
        tv = manager.get_tactical_view()
        # All three lines present, in chronological order (oldest at top)
        first_pos = tv.index("first")
        second_pos = tv.index("second")
        third_pos = tv.index("third")
        assert first_pos < second_pos < third_pos
        # Still single header
        assert tv.count("# Tactical View") == 1

    def test_caps_at_max_entries_dropping_oldest(self, manager):
        # 16 entries, cap at default 14 — first two should be dropped
        for i in range(1, 17):
            manager.append_tactical_observation(f"2026-04-{i:02d}", f"obs {i}")
        tv = manager.get_tactical_view()
        # Oldest two dropped
        assert "obs 1" not in tv.split("obs 10")[0]  # not in first 9 entries either
        assert "- 2026-04-01: " not in tv
        assert "- 2026-04-02: " not in tv
        # Newest 14 retained
        for i in range(3, 17):
            assert f"- 2026-04-{i:02d}: " in tv

    def test_custom_max_entries(self, manager):
        for i in range(1, 6):
            manager.append_tactical_observation(f"2026-04-0{i}", f"obs {i}", max_entries=3)
        tv = manager.get_tactical_view()
        # Only last 3 kept
        assert "- 2026-04-01: " not in tv
        assert "- 2026-04-02: " not in tv
        assert "- 2026-04-03: " in tv
        assert "- 2026-04-04: " in tv
        assert "- 2026-04-05: " in tv

    def test_empty_observation_is_noop(self, manager):
        manager.append_tactical_observation("2026-04-01", "first")
        manager.append_tactical_observation("2026-04-02", "")
        manager.append_tactical_observation("2026-04-02", "   ")
        tv = manager.get_tactical_view()
        assert "first" in tv
        # Only one entry remains
        assert tv.count("- 2026-04-") == 1

    def test_self_heals_duplicate_headers(self, manager):
        """The old append-the-whole-file pattern produced duplicated headers.
        The new method rebuilds from parsed entries only, so legacy files
        with stacked '# Tactical View' lines self-heal on next append."""
        # Write a file with the legacy duplicate-header artifact
        manager._write(
            "tactical_view",
            "# Tactical View\n\n# Tactical View\n\n# Tactical View\n\n- 2026-04-01: legacy\n",
        )
        manager.append_tactical_observation("2026-04-02", "fresh")
        tv = manager.get_tactical_view()
        assert tv.count("# Tactical View") == 1
        assert "- 2026-04-01: legacy" in tv
        assert "- 2026-04-02: fresh" in tv

    def test_multiple_observations_same_day_both_kept(self, manager):
        manager.append_tactical_observation("2026-04-01", "morning take")
        manager.append_tactical_observation("2026-04-01", "afternoon take")
        tv = manager.get_tactical_view()
        assert "morning take" in tv
        assert "afternoon take" in tv


class TestClearAll:
    def test_clear_all(self, manager):
        manager.add_thesis(
            ticker="AAPL", direction="LONG", thesis="test",
            entry_price=100.0, target_price=150.0, stop_price=80.0,
        )
        manager.update_position("AAPL", "LONG", 5, 100.0, 500.0, "2024-01-10")
        manager.append_lesson("test lesson")
        manager.add_theme("AI", "Artificial intelligence", score=4)
        manager.add_belief("Test Belief", "A principle")

        manager.clear_all()

        assert manager.get_all_theses() == []
        assert manager.get_holdings() == []
        assert manager.get_all_lessons() == []
        assert manager.get_all_themes() == []
        assert manager.get_all_beliefs() == []
