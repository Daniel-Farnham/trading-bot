"""Tests for live trading prompt builders and daily state."""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from src.live.portfolio_state import (
    AccountState, Performance, PortfolioSnapshot, PositionRow,
)
from src.live.prompts import (
    build_call1_prompt, build_call3_prompt, format_portfolio_block,
    _format_call1_for_call3,
)
from src.live.daily_state import DailyState


def _snapshot(
    *, equity=100000.0, cash=30000.0, position_count=3, max_positions=8,
    min_cash_pct=0.05, positions=None,
) -> PortfolioSnapshot:
    """Build a snapshot with sensible defaults; pass overrides to test branches."""
    cash_reserve = round(equity * min_cash_pct, 2)
    available = max(0.0, round(cash - cash_reserve, 2))
    return PortfolioSnapshot(
        account=AccountState(
            equity=equity, cash=cash, cash_reserve=cash_reserve,
            available_for_new_buys=available, position_count=position_count,
            max_positions=max_positions, min_cash_pct=min_cash_pct,
            at_max_positions=position_count >= max_positions,
            over_limit=max(0, position_count - max_positions),
        ),
        performance=Performance(
            total_return_pct=8.0, spy_return_pct=2.0, return_vs_spy=6.0,
            unrealized_pnl=1500.0, inception_date="2026-04-05",
            initial_value=100000.0, spy_price=510.0,
        ),
        positions=positions or [],
    )


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


class TestFormatPortfolioBlock:
    def test_under_limit_shows_slots_available(self):
        snap = _snapshot(position_count=5, max_positions=8, cash=30000.0)
        block = format_portfolio_block(snap)
        assert "Position slots available: 3" in block
        assert "OVER POSITION LIMIT" not in block
        assert "AT POSITION LIMIT" not in block

    def test_at_limit_blocks_unmatched_buys(self):
        snap = _snapshot(position_count=8, max_positions=8, cash=30000.0)
        block = format_portfolio_block(snap)
        assert "AT POSITION LIMIT" in block
        assert "matching CLOSE/REDUCE in the same response" in block
        assert "OVER POSITION LIMIT" not in block

    def test_over_limit_priority_addendum(self):
        # Reproduces the user's actual state: 10/8 positions
        snap = _snapshot(position_count=10, max_positions=8, cash=-24000.0)
        block = format_portfolio_block(snap)
        assert "OVER POSITION LIMIT" in block
        assert "10/8" in block
        assert "TOP PRIORITY" in block
        assert "at least 2 position(s)" in block
        assert "Do NOT propose any new BUY or PYRAMID" in block

    def test_negative_cash_shows_zero_buying_power(self):
        snap = _snapshot(equity=108384.53, cash=-24144.61, position_count=10)
        block = format_portfolio_block(snap)
        assert "Buying power for new buys: $0.00" in block
        assert "Cash: $-24,144.61" in block

    def test_cash_reserve_line_present(self):
        snap = _snapshot(equity=100000.0, min_cash_pct=0.05)
        block = format_portfolio_block(snap)
        assert "Cash reserve required: $5,000.00 (5% of equity)" in block
        assert "Min cash reserve: $5,000.00" in block

    def test_position_table_includes_day_change_and_pnl(self):
        snap = _snapshot(
            equity=100000.0, cash=30000.0, position_count=1,
            positions=[PositionRow(
                ticker="MU", side="long", qty=50,
                avg_entry=400.0, current_price=487.0, market_value=24350.0,
                day_change_pct=1.2, unrealized_pnl=4350.0,
                unrealized_pnl_pct=21.75, pct_of_portfolio=24.35,
            )],
        )
        block = format_portfolio_block(snap)
        assert "MU" in block
        assert "+1.20%" in block  # day change
        assert "+15.9%" not in block  # not a fake number
        assert "+21.8%" in block or "+21.75%" in block  # unrealized P&L %
        assert "24.4%" in block or "24.3%" in block or "24.35%" in block

    def test_spy_comparison_present(self):
        snap = _snapshot(equity=100000.0, cash=30000.0)
        # Default Performance: bot +8%, SPY +2%, vs SPY +6pp
        block = format_portfolio_block(snap)
        assert "+8.00%" in block
        assert "SPY: +2.00%" in block
        assert "vs SPY: +6.00pp" in block

    def test_hard_constraints_section_always_present(self):
        snap = _snapshot()
        block = format_portfolio_block(snap)
        assert "## HARD CONSTRAINTS — you MUST honor these" in block
        assert "No margin: do NOT propose trades that would require negative cash." in block
        assert "Cash math:" in block
        assert "decision_reasoning" in block


class TestBuildCall3PromptWithSnapshot:
    def test_portfolio_block_inserted_above_portfolio_state(self):
        engine = MagicMock()
        engine._build_prompt.return_value = (
            "You are the CIO...\n\nPORTFOLIO STATE:\n- Value: $100k"
        )
        snap = _snapshot(position_count=10, max_positions=8, cash=-24000.0)

        result = build_call3_prompt(
            decision_engine=engine, sim_date="2026-04-22",
            memory_context="", world_state="", technicals_summary="",
            fundamentals_summary="", portfolio_value=100000, cash=-24000,
            portfolio_snapshot=snap,
        )

        assert "CURRENT PORTFOLIO STATE (LIVE FROM ALPACA" in result
        assert "OVER POSITION LIMIT" in result
        # The new block must come BEFORE the engine's PORTFOLIO STATE marker
        block_pos = result.index("CURRENT PORTFOLIO STATE (LIVE FROM ALPACA")
        portfolio_pos = result.index("PORTFOLIO STATE:")
        assert block_pos < portfolio_pos

    def test_no_snapshot_means_no_block(self):
        engine = MagicMock()
        engine._build_prompt.return_value = "PORTFOLIO STATE:\nbase"
        result = build_call3_prompt(
            decision_engine=engine, sim_date="2026-04-22",
            memory_context="", world_state="", technicals_summary="",
            fundamentals_summary="", portfolio_value=100000, cash=30000,
            portfolio_snapshot=None,
        )
        assert "CURRENT PORTFOLIO STATE (LIVE FROM ALPACA" not in result

    def test_snapshot_and_call1_both_present(self):
        engine = MagicMock()
        engine._build_prompt.return_value = "PORTFOLIO STATE:\nbase"
        snap = _snapshot(position_count=3, max_positions=8, cash=30000.0)

        result = build_call3_prompt(
            decision_engine=engine, sim_date="2026-04-22",
            memory_context="", world_state="", technicals_summary="",
            fundamentals_summary="", portfolio_value=100000, cash=30000,
            call1_output={"macro_assessment": "Risk-on macro"},
            portfolio_snapshot=snap,
        )

        # Both sections present, both before the PORTFOLIO STATE marker
        assert "CURRENT PORTFOLIO STATE (LIVE FROM ALPACA" in result
        assert "TODAY'S DISCOVERY" in result
        assert result.index("CURRENT PORTFOLIO STATE") < result.index("PORTFOLIO STATE:")
        assert result.index("TODAY'S DISCOVERY") < result.index("PORTFOLIO STATE:")


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
