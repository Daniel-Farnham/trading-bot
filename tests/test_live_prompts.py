"""Tests for live trading prompt builders and daily state."""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from src.live.prompts import build_call1_prompt, build_call3_prompt, _format_call1_for_call3
from src.live.daily_state import DailyState


class TestBuildCall1Prompt:
    def test_contains_today_date(self):
        prompt = build_call1_prompt(
            themes_md="AI Infra [4]",
            holdings_tickers=["NVDA"],
            watchlist_tickers=["CEG"],
            universe_tickers=["NVDA", "AAPL"],
            world_view_md="Risk-on",
        )
        assert date.today().isoformat() in prompt

    def test_contains_holdings(self):
        prompt = build_call1_prompt(
            themes_md="", holdings_tickers=["NVDA", "AVGO"],
            watchlist_tickers=[], universe_tickers=[], world_view_md="",
        )
        assert "NVDA" in prompt
        assert "AVGO" in prompt

    def test_empty_holdings_shows_message(self):
        prompt = build_call1_prompt(
            themes_md="", holdings_tickers=[],
            watchlist_tickers=[], universe_tickers=[], world_view_md="",
        )
        assert "No current holdings" in prompt

    def test_contains_themes(self):
        prompt = build_call1_prompt(
            themes_md="AI Infrastructure [4]\nNuclear Renaissance [3]",
            holdings_tickers=[], watchlist_tickers=[],
            universe_tickers=[], world_view_md="",
        )
        assert "AI Infrastructure" in prompt
        assert "Nuclear Renaissance" in prompt

    def test_contains_world_view(self):
        prompt = build_call1_prompt(
            themes_md="", holdings_tickers=[], watchlist_tickers=[],
            universe_tickers=[], world_view_md="Fed higher-for-longer",
        )
        assert "Fed higher-for-longer" in prompt

    def test_contains_universe_count(self):
        tickers = [f"TICK{i}" for i in range(50)]
        prompt = build_call1_prompt(
            themes_md="", holdings_tickers=[], watchlist_tickers=[],
            universe_tickers=tickers, world_view_md="",
        )
        assert "50 stocks" in prompt

    def test_contains_json_schema(self):
        prompt = build_call1_prompt(
            themes_md="", holdings_tickers=[], watchlist_tickers=[],
            universe_tickers=[], world_view_md="",
        )
        assert "macro_assessment" in prompt
        assert "new_universe_additions" in prompt
        assert "holdings_alerts" in prompt
        assert "emerging_signals" in prompt

    def test_mentions_research_tools(self):
        prompt = build_call1_prompt(
            themes_md="", holdings_tickers=[], watchlist_tickers=[],
            universe_tickers=[], world_view_md="",
        )
        assert "search_news" in prompt
        assert "get_fundamentals" in prompt
        assert "screen_by_theme" in prompt

    def test_contains_watchlist(self):
        prompt = build_call1_prompt(
            themes_md="", holdings_tickers=[],
            watchlist_tickers=["CEG", "VST"],
            universe_tickers=[], world_view_md="",
        )
        assert "CEG" in prompt
        assert "VST" in prompt


class TestBuildCall3Prompt:
    def test_delegates_to_decision_engine(self):
        engine = MagicMock()
        engine._build_prompt.return_value = "PORTFOLIO STATE:\nBase prompt content"

        result = build_call3_prompt(
            decision_engine=engine,
            sim_date="2025-04-03",
            memory_context="memory",
            world_state="news",
            technicals_summary="technicals",
            fundamentals_summary="fundamentals",
            portfolio_value=100000,
            cash=30000,
        )

        engine._build_prompt.assert_called_once()
        assert "Base prompt content" in result

    def test_without_call1_output(self):
        engine = MagicMock()
        engine._build_prompt.return_value = "Base prompt"

        result = build_call3_prompt(
            decision_engine=engine,
            sim_date="2025-04-03",
            memory_context="", world_state="", technicals_summary="",
            fundamentals_summary="", portfolio_value=100000, cash=30000,
            call1_output=None,
        )

        assert result == "Base prompt"
        assert "TODAY'S DISCOVERY" not in result

    def test_with_call1_output_inserts_before_portfolio(self):
        engine = MagicMock()
        engine._build_prompt.return_value = (
            "You are the CIO...\n\nPORTFOLIO STATE:\n- Value: $100k"
        )

        call1 = {
            "macro_assessment": "Fed held rates steady",
            "theme_impacts": [
                {"theme": "AI Infra", "direction": "strengthening", "evidence": "MSFT capex up"}
            ],
        }

        result = build_call3_prompt(
            decision_engine=engine,
            sim_date="2025-04-03",
            memory_context="", world_state="", technicals_summary="",
            fundamentals_summary="", portfolio_value=100000, cash=30000,
            call1_output=call1,
        )

        assert "TODAY'S DISCOVERY" in result
        assert "Fed held rates steady" in result
        assert "AI Infra" in result
        # Discovery section should come before portfolio state
        discovery_pos = result.index("TODAY'S DISCOVERY")
        portfolio_pos = result.index("PORTFOLIO STATE:")
        assert discovery_pos < portfolio_pos

    def test_passes_review_type_to_engine(self):
        engine = MagicMock()
        engine._build_prompt.return_value = "prompt"

        build_call3_prompt(
            decision_engine=engine,
            sim_date="2025-04-03",
            memory_context="", world_state="", technicals_summary="",
            fundamentals_summary="", portfolio_value=100000, cash=30000,
            review_type="volatility",
        )

        kwargs = engine._build_prompt.call_args
        assert kwargs[1]["review_type"] == "volatility" or kwargs.kwargs["review_type"] == "volatility"

    def test_passes_options_context(self):
        engine = MagicMock()
        engine._build_prompt.return_value = "prompt"

        build_call3_prompt(
            decision_engine=engine,
            sim_date="2025-04-03",
            memory_context="", world_state="", technicals_summary="",
            fundamentals_summary="", portfolio_value=100000, cash=30000,
            options_context="NVDA call expiring in 20 days",
        )

        kwargs = engine._build_prompt.call_args
        assert "NVDA call expiring" in str(kwargs)


class TestFormatCall1ForCall3:
    def test_formats_macro_assessment(self):
        result = _format_call1_for_call3({
            "macro_assessment": "Tariff fears easing",
        })
        assert "Tariff fears easing" in result

    def test_formats_theme_impacts(self):
        result = _format_call1_for_call3({
            "theme_impacts": [
                {"theme": "AI Infra", "direction": "strengthening", "evidence": "NVDA beat"}
            ],
        })
        assert "AI Infra" in result
        assert "strengthening" in result

    def test_formats_flagged_tickers(self):
        result = _format_call1_for_call3({
            "flagged_tickers_universe": [
                {"ticker": "NVDA", "reason": "Blackwell news"}
            ],
        })
        assert "NVDA" in result
        assert "Blackwell" in result

    def test_formats_new_universe_additions(self):
        result = _format_call1_for_call3({
            "new_universe_additions": [
                {"ticker": "VRT", "reason": "Data center cooling"}
            ],
        })
        assert "VRT" in result
        assert "Newly Added" in result

    def test_formats_holdings_alerts(self):
        result = _format_call1_for_call3({
            "holdings_alerts": [
                {"ticker": "AVGO", "alert": "Earnings beat"}
            ],
        })
        assert "AVGO" in result
        assert "Holdings Alerts" in result

    def test_formats_emerging_signals(self):
        result = _format_call1_for_call3({
            "emerging_signals": [
                {"signal": "Defense backlogs rising", "potential_theme": "Defense Supercycle"}
            ],
        })
        assert "Defense backlogs" in result
        assert "Defense Supercycle" in result

    def test_empty_output(self):
        result = _format_call1_for_call3({})
        assert "TODAY'S DISCOVERY" in result

    def test_full_output(self):
        result = _format_call1_for_call3({
            "macro_assessment": "Markets steady",
            "theme_impacts": [{"theme": "AI", "direction": "up", "evidence": "data"}],
            "flagged_tickers_universe": [{"ticker": "NVDA", "reason": "news"}],
            "new_universe_additions": [{"ticker": "VRT", "reason": "cooling"}],
            "holdings_alerts": [{"ticker": "AVGO", "alert": "beat"}],
            "watchlist_alerts": [{"ticker": "CEG", "alert": "deal"}],
            "emerging_signals": [{"signal": "defense", "potential_theme": "Defense"}],
        })
        assert "Macro:" in result
        assert "Theme Impacts:" in result
        assert "Flagged Tickers:" in result
        assert "Newly Added" in result
        assert "Holdings Alerts:" in result
        assert "Watchlist Alerts:" in result
        assert "Emerging Signals:" in result


class TestDailyState:
    def test_create_default(self):
        state = DailyState()
        assert state.date == ""
        assert state.call1_output is None
        assert state.call3_output is None
        assert state.triggers_fired == []
        assert state.trades_executed == []

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "daily_state.json"
        state = DailyState(date="2025-04-03")
        state.call1_output = {"macro_assessment": "test"}
        state.add_trigger("intraday_shock", "NVDA down 12%", ["NVDA"])
        state.add_trade({"ticker": "NVDA", "action": "SELL", "quantity": 10})
        state.save(path)

        loaded = DailyState.load(path)
        assert loaded.date == "2025-04-03"
        assert loaded.call1_output == {"macro_assessment": "test"}
        assert len(loaded.triggers_fired) == 1
        assert loaded.triggers_fired[0]["trigger_type"] == "intraday_shock"
        assert len(loaded.trades_executed) == 1
        assert loaded.trades_executed[0]["ticker"] == "NVDA"

    def test_load_missing_file(self, tmp_path):
        state = DailyState.load(tmp_path / "nonexistent.json")
        assert state.date == date.today().isoformat()
        assert state.call1_output is None

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "daily_state.json"
        path.write_text("not valid json{{{")
        state = DailyState.load(path)
        assert state.date == date.today().isoformat()

    def test_reset_for_day(self):
        state = DailyState(date="2025-04-02")
        state.call1_output = {"old": "data"}
        state.call3_output = {"old": "data"}
        state.triggers_fired = [{"type": "old"}]
        state.trades_executed = [{"old": "trade"}]

        state.reset_for_day()

        assert state.date == date.today().isoformat()
        assert state.call1_output is None
        assert state.call3_output is None
        assert state.triggers_fired == []
        assert state.trades_executed == []

    def test_is_current_day(self):
        state = DailyState(date=date.today().isoformat())
        assert state.is_current_day() is True

        state.date = "2020-01-01"
        assert state.is_current_day() is False

    def test_add_trigger(self):
        state = DailyState()
        state.add_trigger("volatility_drift", "Portfolio swung 7%", ["NVDA", "AVGO"])

        assert len(state.triggers_fired) == 1
        assert state.triggers_fired[0]["trigger_type"] == "volatility_drift"
        assert state.triggers_fired[0]["tickers"] == ["NVDA", "AVGO"]

    def test_add_trade(self):
        state = DailyState()
        state.add_trade({"ticker": "AAPL", "action": "BUY", "quantity": 50})

        assert len(state.trades_executed) == 1
        assert state.trades_executed[0]["ticker"] == "AAPL"

    def test_persistence_survives_restart(self, tmp_path):
        path = tmp_path / "state.json"

        # Day starts, Call 1 runs
        s1 = DailyState(date=date.today().isoformat())
        s1.call1_output = {"macro_assessment": "Fed meeting today"}
        s1.save(path)

        # Simulate restart
        s2 = DailyState.load(path)
        assert s2.call1_output == {"macro_assessment": "Fed meeting today"}
        assert s2.is_current_day()

        # Trigger fires, Call 3 runs
        s2.add_trigger("intraday_shock", "Portfolio down 6%")
        s2.call3_output = {"weekly_summary": "Emergency review"}
        s2.save(path)

        # Verify full state persisted
        s3 = DailyState.load(path)
        assert s3.call1_output is not None
        assert s3.call3_output is not None
        assert len(s3.triggers_fired) == 1
