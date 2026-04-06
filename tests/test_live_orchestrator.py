"""Tests for live orchestrator, trigger check, and scheduler."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.live.trigger_check import TriggerCheck, TriggerResult
from src.live.scheduler import _is_third_friday


# === Trigger Check Tests ===


@pytest.fixture
def trigger():
    market = MagicMock()
    technicals = MagicMock()
    tc = TriggerCheck(market_data=market, technical_analyzer=technicals)
    return tc


class TestIntradayShock:
    def test_shock_on_large_drop(self, trigger):
        trigger._prev_prices = {"NVDA": 150.0}
        trigger._market.get_latest_prices.return_value = {"NVDA": 120.0, "SPY": 500.0}
        # Mock ATR to return None so fallback threshold (-10%) is used
        trigger._get_atr_pct = MagicMock(return_value=None)
        trigger._get_spy_hv_percentile = MagicMock(return_value=50.0)

        result = trigger.check(
            holdings_tickers=["NVDA"],
            watchlist_tickers=[],
            portfolio_value=100000,
        )

        assert result is not None
        assert result.trigger_type == "intraday_shock"
        assert "NVDA" in result.triggered_tickers

    def test_no_shock_on_small_move(self, trigger):
        trigger._prev_prices = {"NVDA": 150.0}
        trigger._market.get_latest_prices.return_value = {"NVDA": 148.0, "SPY": 500.0}
        trigger._get_atr_pct = MagicMock(return_value=None)
        trigger._get_spy_hv_percentile = MagicMock(return_value=50.0)

        result = trigger.check(
            holdings_tickers=["NVDA"],
            watchlist_tickers=[],
            portfolio_value=100000,
        )

        assert result is None

    def test_shock_on_watchlist_ticker(self, trigger):
        trigger._prev_prices = {"CEG": 200.0}
        trigger._market.get_latest_prices.return_value = {"CEG": 160.0, "SPY": 500.0}
        trigger._get_atr_pct = MagicMock(return_value=None)
        trigger._get_spy_hv_percentile = MagicMock(return_value=50.0)

        result = trigger.check(
            holdings_tickers=[],
            watchlist_tickers=["CEG"],
            portfolio_value=100000,
        )

        assert result is not None
        assert "CEG" in result.triggered_tickers

    def test_first_check_no_prev_prices(self, trigger):
        trigger._market.get_latest_prices.return_value = {"NVDA": 150.0, "SPY": 500.0}
        trigger._get_spy_hv_percentile = MagicMock(return_value=50.0)

        result = trigger.check(
            holdings_tickers=["NVDA"],
            watchlist_tickers=[],
            portfolio_value=100000,
        )

        # No trigger on first check (no reference prices)
        assert result is None

    def test_portfolio_drop_triggers(self, trigger):
        trigger._prev_prices = {"NVDA": 150.0}
        trigger._last_call3_portfolio_value = 100000
        trigger._market.get_latest_prices.return_value = {"NVDA": 148.0, "SPY": 500.0}
        trigger._get_atr_pct = MagicMock(return_value=None)
        trigger._get_spy_hv_percentile = MagicMock(return_value=50.0)

        # Portfolio dropped 6%
        result = trigger.check(
            holdings_tickers=["NVDA"],
            watchlist_tickers=[],
            portfolio_value=94000,
        )

        assert result is not None
        assert result.trigger_type == "intraday_shock"
        assert "Portfolio" in result.details


class TestVolatilityDrift:
    def test_drift_triggers_on_5pct_swing(self, trigger):
        trigger._last_call3_portfolio_value = 100000
        trigger._prev_prices = {"NVDA": 150.0}
        trigger._market.get_latest_prices.return_value = {"NVDA": 150.0, "SPY": 500.0}
        trigger._get_atr_pct = MagicMock(return_value=None)
        trigger._get_spy_hv_percentile = MagicMock(return_value=50.0)

        result = trigger.check(
            holdings_tickers=["NVDA"],
            watchlist_tickers=[],
            portfolio_value=106000,  # 6% up
        )

        assert result is not None
        assert result.trigger_type == "volatility_drift"

    def test_no_drift_under_threshold(self, trigger):
        trigger._last_call3_portfolio_value = 100000
        trigger._prev_prices = {"NVDA": 150.0}
        trigger._market.get_latest_prices.return_value = {"NVDA": 150.0, "SPY": 500.0}
        trigger._get_atr_pct = MagicMock(return_value=None)
        trigger._get_spy_hv_percentile = MagicMock(return_value=50.0)

        result = trigger.check(
            holdings_tickers=["NVDA"],
            watchlist_tickers=[],
            portfolio_value=103000,  # 3% up — under threshold
        )

        assert result is None

    def test_no_drift_without_reference(self, trigger):
        trigger._last_call3_portfolio_value = None
        trigger._prev_prices = {"NVDA": 150.0}
        trigger._market.get_latest_prices.return_value = {"NVDA": 150.0, "SPY": 500.0}
        trigger._get_atr_pct = MagicMock(return_value=None)
        trigger._get_spy_hv_percentile = MagicMock(return_value=50.0)

        result = trigger.check(
            holdings_tickers=["NVDA"],
            watchlist_tickers=[],
            portfolio_value=110000,
        )

        assert result is None


class TestLowVol:
    def test_low_vol_triggers(self, trigger):
        trigger._spy_hv_prev = 50.0  # Was above threshold
        trigger._prev_prices = {"NVDA": 150.0}
        trigger._market.get_latest_prices.return_value = {"NVDA": 150.0, "SPY": 500.0}
        trigger._get_atr_pct = MagicMock(return_value=None)
        trigger._get_spy_hv_percentile = MagicMock(return_value=20.0)  # Below 30

        result = trigger.check(
            holdings_tickers=["NVDA"],
            watchlist_tickers=[],
            portfolio_value=100000,
        )

        assert result is not None
        assert result.trigger_type == "low_volatility"

    def test_low_vol_debounce(self, trigger):
        trigger._spy_hv_prev = 20.0  # Already in calm period
        trigger._prev_prices = {"NVDA": 150.0}
        trigger._market.get_latest_prices.return_value = {"NVDA": 150.0, "SPY": 500.0}
        trigger._get_atr_pct = MagicMock(return_value=None)
        trigger._get_spy_hv_percentile = MagicMock(return_value=18.0)

        result = trigger.check(
            holdings_tickers=["NVDA"],
            watchlist_tickers=[],
            portfolio_value=100000,
        )

        # Should NOT trigger again — debounced
        assert result is None


class TestSetReference:
    def test_set_last_call3_value(self, trigger):
        trigger.set_last_call3_value(105000)
        assert trigger._last_call3_portfolio_value == 105000


# === Scheduler Tests ===


class TestIsThirdFriday:
    def test_third_friday(self):
        # 2025-04-18 is a Friday and the 18th (15 <= 18 <= 21)
        assert _is_third_friday(date(2025, 4, 18)) is True

    def test_first_friday(self):
        assert _is_third_friday(date(2025, 4, 4)) is False

    def test_not_friday(self):
        assert _is_third_friday(date(2025, 4, 16)) is False  # Wednesday

    def test_fourth_friday(self):
        assert _is_third_friday(date(2025, 4, 25)) is False


# === Orchestrator Tests ===


class TestOrchestratorCall1:
    def test_call1_processes_output(self):
        orchestrator = _make_orchestrator()
        orchestrator._market.get_account.return_value = {
            "portfolio_value": 100000, "cash": 50000,
        }
        orchestrator._market.get_positions.return_value = []
        orchestrator._claude.call.return_value = {
            "macro_assessment": "Markets steady",
            "flagged_tickers_universe": [
                {"ticker": "NVDA", "reason": "Blackwell news"},
            ],
            "new_universe_additions": [
                {"ticker": "VRT", "reason": "Data center cooling"},
            ],
            "world_view_observation": "Fed held rates",
        }

        orchestrator.run_call1()

        orchestrator._claude.call.assert_called_once()
        orchestrator._watchlist.add.assert_any_call("NVDA", source="call1", reason="Blackwell news")
        orchestrator._universe.add.assert_called_with("VRT", source="call1", reason="Data center cooling")
        orchestrator._notifier.send_call1_summary.assert_called_once()

    def test_call1_budget_exceeded(self):
        from src.live.claude_client import BudgetExceededError
        orchestrator = _make_orchestrator()
        orchestrator._claude.call.side_effect = BudgetExceededError("Daily cap")

        orchestrator.run_call1()

        orchestrator._notifier.send_error.assert_called_once()

    def test_call1_empty_result(self):
        orchestrator = _make_orchestrator()
        orchestrator._claude.call.return_value = None

        orchestrator.run_call1()

        orchestrator._notifier.send_call1_summary.assert_not_called()


class TestOrchestratorTriggerCheck:
    def test_trigger_fires_call3(self):
        orchestrator = _make_orchestrator()
        orchestrator._trigger.check.return_value = TriggerResult(
            trigger_type="intraday_shock",
            details="NVDA down 12%",
            triggered_tickers=["NVDA"],
        )
        orchestrator._market.get_account.return_value = {
            "portfolio_value": 100000, "cash": 30000,
        }
        # Mock run_call3 to prevent it actually running
        orchestrator.run_call3 = MagicMock()

        orchestrator.run_trigger_check()

        orchestrator.run_call3.assert_called_once_with(
            review_type="intraday_shock",
            trigger_reason="NVDA down 12%",
        )
        orchestrator._notifier.send_alert.assert_called_once()

    def test_no_trigger_no_call3(self):
        orchestrator = _make_orchestrator()
        orchestrator._trigger.check.return_value = None
        orchestrator._market.get_account.return_value = {
            "portfolio_value": 100000, "cash": 30000,
        }
        orchestrator.run_call3 = MagicMock()

        orchestrator.run_trigger_check()

        orchestrator.run_call3.assert_not_called()


class TestOrchestratorCall3:
    def test_call3_executes_trades(self):
        orchestrator = _make_orchestrator()
        orchestrator._market.get_account.return_value = {
            "portfolio_value": 100000, "cash": 30000,
        }
        orchestrator._market.get_positions.return_value = []
        orchestrator._claude.call.return_value = {
            "world_assessment": "AI capex intact",
            "new_positions": [],
            "close_positions": [],
            "reduce_positions": [],
            "weekly_summary": "Quiet week",
        }
        orchestrator._executor.execute_decisions.return_value = []

        orchestrator.run_call3(review_type="weekly")

        orchestrator._claude.call.assert_called_once()
        orchestrator._executor.execute_decisions.assert_called_once()
        orchestrator._notifier.send_call3_summary.assert_called_once()
        orchestrator._trigger.set_last_call3_value.assert_called_with(100000)


class TestOrchestratorEOD:
    def test_eod_sends_email(self):
        orchestrator = _make_orchestrator()
        orchestrator._market.get_account.return_value = {
            "equity": 105000, "cash": 30000, "buying_power": 60000,
            "portfolio_value": 105000,
        }
        orchestrator._market.get_positions.return_value = [
            {"ticker": "NVDA", "qty": 80, "avg_entry": 125, "current_price": 155,
             "market_value": 12400, "unrealized_pnl": 2400, "unrealized_pnl_pct": 0.24},
        ]
        orchestrator._tm.get_data_dir.return_value = "/tmp/test"

        orchestrator.run_eod_portfolio()

        orchestrator._notifier.send_eod_portfolio.assert_called_once()


class TestReconcile:
    def test_reconcile_matches(self):
        orchestrator = _make_orchestrator()
        orchestrator._market.get_positions.return_value = [
            {"ticker": "NVDA"}, {"ticker": "AVGO"},
        ]
        orchestrator._tm.get_holdings.return_value = [
            {"ticker": "NVDA"}, {"ticker": "AVGO"},
        ]
        orchestrator._market.get_account.return_value = {"portfolio_value": 100000}

        # Should not raise
        orchestrator.reconcile_on_startup()
        orchestrator._trigger.set_last_call3_value.assert_called_with(100000)


def _make_orchestrator():
    """Create an orchestrator with all dependencies mocked."""
    from src.live.orchestrator import LiveOrchestrator

    orchestrator = LiveOrchestrator.__new__(LiveOrchestrator)
    orchestrator._claude = MagicMock()
    orchestrator._engine = MagicMock()
    orchestrator._engine._build_prompt.return_value = "PORTFOLIO STATE:\ntest prompt"
    orchestrator._tm = MagicMock()
    orchestrator._tm.get_holdings.return_value = []
    orchestrator._tm.get_all_themes.return_value = []
    orchestrator._tm.get_world_view.return_value = ""
    orchestrator._tm.get_decision_context.return_value = "memory"
    orchestrator._tm._paths = {"theses": Path("/tmp/test/active_theses.md")}
    orchestrator._market = MagicMock()
    orchestrator._technicals = MagicMock()
    orchestrator._fundamentals = MagicMock()
    orchestrator._news = MagicMock()
    orchestrator._news.get_news.return_value = []
    orchestrator._trigger = MagicMock()
    orchestrator._trigger.check.return_value = None
    orchestrator._executor = MagicMock()
    orchestrator._executor.execute_decisions.return_value = []
    orchestrator._watchlist = MagicMock()
    orchestrator._watchlist.get_tickers.return_value = []
    orchestrator._watchlist.prune.return_value = []
    orchestrator._universe = MagicMock()
    orchestrator._universe.get_tickers.return_value = []
    orchestrator._notifier = MagicMock()
    orchestrator._state = MagicMock()
    orchestrator._state.call1_output = None
    orchestrator._state.is_current_day.return_value = True
    orchestrator._state_path = "/tmp/test_state.json"
    orchestrator._review_count = 0
    orchestrator._pending = MagicMock()
    orchestrator._pending.get_all.return_value = []
    orchestrator._reconciler = MagicMock()
    orchestrator._reconciler.reconcile.return_value = {
        "orders_filled": [], "orders_retried": [], "orders_failed": [],
        "ledger_synced": True, "positions_added": [], "positions_removed": [],
    }
    return orchestrator
