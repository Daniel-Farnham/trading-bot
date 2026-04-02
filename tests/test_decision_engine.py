from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.strategy.decision_engine import DecisionEngine
from src.strategy.thesis_manager import ThesisManager


@pytest.fixture
def manager(tmp_path):
    mgr = ThesisManager.__new__(ThesisManager)
    mgr._paths = {
        "theses": tmp_path / "active_theses.md",
        "ledger": tmp_path / "portfolio_ledger.md",
        "summaries": tmp_path / "quarterly_summaries.md",
        "lessons": tmp_path / "lessons_learned.md",
        "themes": tmp_path / "themes.md",
        "beliefs": tmp_path / "beliefs.md",
        "world_view": tmp_path / "world_view.md",
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
def engine(manager):
    return DecisionEngine(thesis_manager=manager)


SAMPLE_RESPONSE = {
    "world_assessment": "AI spending continues to accelerate",
    "thesis_updates": [
        {"ticker": "NVDA", "status": "ACTIVE", "notes": "Q1 confirmed thesis"},
    ],
    "new_positions": [
        {
            "ticker": "CRWD",
            "action": "BUY",
            "allocation_pct": 6,
            "direction": "LONG",
            "thesis": "Cybersecurity demand growing",
            "invalidation": "Growth drops below 20%",
            "target_price": 400.0,
            "stop_price": 250.0,
            "horizon": "3-6 months",
            "confidence": "high",
            "timing_note": "RSI at 35",
        }
    ],
    "close_positions": [
        {"ticker": "TSLA", "reason": "EV margin thesis broken"},
    ],
    "reduce_positions": [],
    "lessons": ["Don't fight the Fed"],
    "weekly_summary": "Strong week for AI names",
}


class TestBuildPrompt:
    def test_contains_anti_future_knowledge(self, engine):
        prompt = engine._build_prompt(
            "2024-03-15", "memory", "world state", "technicals", "", 100000, 50000,
        )
        assert "2024-03-15" in prompt
        assert "DO NOT know what happens after" in prompt

    def test_contains_portfolio_state(self, engine):
        prompt = engine._build_prompt(
            "2024-03-15", "memory", "world state", "technicals", "", 108000, 34000,
        )
        assert "$108,000" in prompt
        assert "$34,000" in prompt

    def test_contains_memory_and_research(self, engine):
        prompt = engine._build_prompt(
            "2024-03-15", "MEMORY CONTENT", "WORLD STATE", "TECH DATA", "FUNDAMENTALS", 100000, 50000,
        )
        assert "MEMORY CONTENT" in prompt
        assert "WORLD STATE" in prompt
        assert "TECH DATA" in prompt


class TestParseResponse:
    @patch("src.strategy.decision_engine.subprocess.run")
    def test_successful_review(self, mock_run, engine, manager):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(SAMPLE_RESPONSE),
            stderr="",
        )

        # Seed a thesis for the update to work
        manager.add_thesis(
            "NVDA", "LONG", "AI chips", 800, 1000, 700,
        )
        manager.add_thesis(
            "TSLA", "LONG", "EVs", 200, 300, 150,
        )

        result = engine.run_weekly_review(
            sim_date="2024-03-15",
            world_state="test world state",
            portfolio_value=100000,
            cash=50000,
        )

        assert result["world_assessment"] == "AI spending continues to accelerate"
        assert len(result["new_positions"]) == 1
        assert result["new_positions"][0]["ticker"] == "CRWD"
        assert len(result["close_positions"]) == 1

    @patch("src.strategy.decision_engine.subprocess.run")
    def test_updates_memory(self, mock_run, engine, manager):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(SAMPLE_RESPONSE),
            stderr="",
        )

        manager.add_thesis("NVDA", "LONG", "AI chips", 800, 1000, 700)
        manager.add_thesis("TSLA", "LONG", "EVs", 200, 300, 150)

        engine.run_weekly_review("2024-03-15", "news", portfolio_value=100000, cash=50000)

        # New thesis should be added
        crwd = manager.get_by_ticker("CRWD")
        assert crwd is not None
        assert crwd["thesis"] == "Cybersecurity demand growing"

        # TSLA thesis should still exist after run_weekly_review —
        # it only gets moved to watching during _execute_decisions (after broker confirms close)
        tsla = manager.get_by_ticker("TSLA")
        assert tsla is not None

        # Lesson should be added
        lessons = manager.get_all_lessons()
        assert any("Fed" in l["content"] for l in lessons)

    @patch("src.strategy.decision_engine.subprocess.run")
    def test_claude_failure_returns_empty(self, mock_run, engine):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error",
        )

        result = engine.run_weekly_review("2024-03-15", "news", portfolio_value=100000, cash=50000)
        assert result["world_assessment"] == ""
        assert result["new_positions"] == []

    @patch("src.strategy.decision_engine.subprocess.run")
    def test_json_in_code_fence(self, mock_run, engine):
        fenced = f"```json\n{json.dumps(SAMPLE_RESPONSE)}\n```"
        mock_run.return_value = MagicMock(
            returncode=0, stdout=fenced, stderr="",
        )

        result = engine.run_weekly_review("2024-03-15", "news", portfolio_value=100000, cash=50000)
        assert result["world_assessment"] == "AI spending continues to accelerate"

    @patch("src.strategy.decision_engine.subprocess.run")
    def test_timeout_returns_empty(self, mock_run, engine):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=600)

        result = engine.run_weekly_review("2024-03-15", "news", portfolio_value=100000, cash=50000)
        assert result == engine._empty_response()
