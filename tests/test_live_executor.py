"""Tests for live trade executor."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.execution.broker import OrderResult
from src.live.executor import LiveExecutor, _find_position


@pytest.fixture
def broker():
    return MagicMock()


@pytest.fixture
def risk():
    mock = MagicMock()
    mock.is_core_position.return_value = False
    mock._min_cash_pct = 0.05
    return mock


@pytest.fixture
def thesis_manager():
    return MagicMock()


@pytest.fixture
def executor(broker, risk, thesis_manager):
    return LiveExecutor(broker=broker, risk_manager=risk, thesis_manager=thesis_manager)


SAMPLE_POSITIONS = [
    {
        "symbol": "NVDA", "qty": "80", "avg_entry_price": "125.00",
        "current_price": "155.00", "market_value": "12400.00",
        "unrealized_pl": "2400.00", "unrealized_plpc": "0.24",
    },
    {
        "symbol": "NKE", "qty": "100", "avg_entry_price": "90.00",
        "current_price": "75.00", "market_value": "7500.00",
        "unrealized_pl": "-1500.00", "unrealized_plpc": "-0.167",
    },
]


class TestClosePositions:
    def test_closes_position(self, executor, broker, thesis_manager):
        broker.close_position.return_value = OrderResult(success=True)

        response = {
            "close_positions": [{"ticker": "NKE", "reason": "Tariff thesis broken"}],
        }
        trades = executor.execute_decisions(response, 100000, 30000, SAMPLE_POSITIONS)

        broker.close_position.assert_called_once_with("NKE")
        thesis_manager.remove_position.assert_called_once_with("NKE")
        thesis_manager.move_to_watching.assert_called_once()
        assert len(trades) == 1
        assert trades[0]["ticker"] == "NKE"
        assert trades[0]["action"] == "CLOSE"

    def test_close_nonexistent_position_skipped(self, executor, broker):
        response = {
            "close_positions": [{"ticker": "AAPL", "reason": "not held"}],
        }
        trades = executor.execute_decisions(response, 100000, 30000, SAMPLE_POSITIONS)

        broker.close_position.assert_not_called()
        assert len(trades) == 0

    def test_close_failure_logged(self, executor, broker, thesis_manager):
        broker.close_position.return_value = OrderResult(success=False, error="API error")

        response = {
            "close_positions": [{"ticker": "NKE", "reason": "broken"}],
        }
        trades = executor.execute_decisions(response, 100000, 30000, SAMPLE_POSITIONS)

        thesis_manager.remove_position.assert_not_called()
        assert len(trades) == 0


class TestReducePositions:
    def test_reduces_position(self, executor, broker):
        broker.place_market_sell.return_value = OrderResult(success=True)

        response = {
            "reduce_positions": [
                {"ticker": "NVDA", "new_allocation_pct": 5, "reason": "Taking some off"},
            ],
        }
        trades = executor.execute_decisions(response, 100000, 30000, SAMPLE_POSITIONS)

        broker.place_market_sell.assert_called_once()
        assert len(trades) == 1
        assert trades[0]["action"] == "REDUCE"

    def test_reduce_nonexistent_skipped(self, executor, broker):
        response = {
            "reduce_positions": [{"ticker": "AAPL", "new_allocation_pct": 3}],
        }
        trades = executor.execute_decisions(response, 100000, 30000, SAMPLE_POSITIONS)

        broker.place_market_sell.assert_not_called()


class TestNewPositions:
    def test_new_scout_position(self, executor, broker, risk):
        mock_plan = MagicMock()
        mock_plan.quantity = 50
        mock_plan.entry_price = 200.0
        mock_plan.catastrophic_stop = 170.0
        mock_plan.position_value = 10000.0
        mock_plan.allocation_pct = 6.0
        mock_plan.side = "LONG"
        risk.evaluate_new_position.return_value = mock_plan
        risk.is_core_position.return_value = False
        broker.place_bracket_order.return_value = OrderResult(success=True)

        response = {
            "new_positions": [{
                "ticker": "CRWD", "action": "BUY", "allocation_pct": 6,
                "direction": "LONG", "confidence": "medium",
                "thesis": "Cybersecurity demand", "stop_price": 180.0,
                "target_price": 250.0,
            }],
        }
        trades = executor.execute_decisions(response, 100000, 30000, [])

        broker.place_bracket_order.assert_called_once()
        assert len(trades) == 1
        assert "SCOUT" in trades[0]["action"]

    def test_new_core_position(self, executor, broker, risk):
        mock_plan = MagicMock()
        mock_plan.quantity = 100
        mock_plan.entry_price = 150.0
        mock_plan.position_value = 15000.0
        mock_plan.allocation_pct = 15.0
        mock_plan.side = "LONG"
        risk.evaluate_new_position.return_value = mock_plan
        risk.is_core_position.return_value = True
        broker.place_market_buy.return_value = OrderResult(success=True)

        response = {
            "new_positions": [{
                "ticker": "AVGO", "action": "BUY", "allocation_pct": 15,
                "direction": "LONG", "confidence": "high",
                "thesis": "AI networking",
            }],
        }
        trades = executor.execute_decisions(response, 100000, 30000, [])

        broker.place_market_buy.assert_called_once_with("AVGO", 100)
        assert len(trades) == 1
        assert "CORE" in trades[0]["action"]

    def test_short_position(self, executor, broker, risk):
        mock_plan = MagicMock()
        mock_plan.quantity = 50
        mock_plan.entry_price = 90.0
        mock_plan.position_value = 4500.0
        mock_plan.allocation_pct = 5.0
        mock_plan.side = "SHORT"
        risk.evaluate_new_position.return_value = mock_plan
        risk.is_core_position.return_value = False
        broker.place_short_sell.return_value = OrderResult(success=True)

        response = {
            "new_positions": [{
                "ticker": "NKE", "action": "SHORT", "allocation_pct": 5,
                "direction": "SHORT", "confidence": "medium",
                "thesis": "Tariff headwinds",
            }],
        }
        trades = executor.execute_decisions(response, 100000, 30000, [])

        broker.place_short_sell.assert_called_once_with("NKE", 50)
        assert len(trades) == 1
        assert trades[0]["action"] == "SHORT"

    def test_risk_veto_blocks_trade(self, executor, broker, risk):
        from src.strategy.risk_v3 import V3RiskVeto
        risk.evaluate_new_position.return_value = V3RiskVeto("CRWD", "Max positions reached")

        response = {
            "new_positions": [{
                "ticker": "CRWD", "action": "BUY", "allocation_pct": 6,
                "direction": "LONG", "confidence": "medium", "thesis": "test",
            }],
        }
        trades = executor.execute_decisions(response, 100000, 30000, [])

        broker.place_market_buy.assert_not_called()
        broker.place_bracket_order.assert_not_called()
        assert len(trades) == 0

    def test_max_new_positions_cap(self, executor, broker, risk):
        mock_plan = MagicMock()
        mock_plan.quantity = 10
        mock_plan.entry_price = 100.0
        mock_plan.catastrophic_stop = 85.0
        mock_plan.position_value = 1000.0
        mock_plan.allocation_pct = 5.0
        mock_plan.side = "LONG"
        risk.evaluate_new_position.return_value = mock_plan
        risk.is_core_position.return_value = False
        broker.place_bracket_order.return_value = OrderResult(success=True)

        response = {
            "new_positions": [
                {"ticker": f"TICK{i}", "action": "BUY", "allocation_pct": 5,
                 "direction": "LONG", "confidence": "medium", "thesis": "test",
                 "stop_price": 90, "target_price": 120}
                for i in range(5)
            ],
        }
        trades = executor.execute_decisions(response, 100000, 50000, [])

        # Max 3 new positions
        assert len(trades) == 3

    def test_options_skipped(self, executor, broker):
        response = {
            "new_positions": [{
                "ticker": "NVDA", "action": "BUY_CALL", "allocation_pct": 5,
                "direction": "LONG", "confidence": "high", "thesis": "AI capex",
            }],
        }
        trades = executor.execute_decisions(response, 100000, 30000, [])

        broker.place_market_buy.assert_not_called()
        assert len(trades) == 0


class TestPyramidUpgrade:
    def test_pyramid_adds_shares(self, executor, broker, risk):
        risk.is_core_position.return_value = True
        broker.place_market_buy.return_value = OrderResult(success=True)

        response = {
            "new_positions": [{
                "ticker": "NVDA", "action": "BUY", "allocation_pct": 25,
                "direction": "LONG", "confidence": "high", "thesis": "Adding to winner",
            }],
        }
        # NVDA is at ~12.4% ($12,400 / $100,000) — requesting 25%
        trades = executor.execute_decisions(response, 100000, 30000, SAMPLE_POSITIONS)

        broker.place_market_buy.assert_called_once()
        assert len(trades) == 1
        assert trades[0]["action"] == "PYRAMID"

    def test_pyramid_small_delta_skipped(self, executor, broker, risk):
        risk.is_core_position.return_value = True

        response = {
            "new_positions": [{
                "ticker": "NVDA", "action": "BUY", "allocation_pct": 13,
                "direction": "LONG", "confidence": "high", "thesis": "Tiny add",
            }],
        }
        # NVDA at ~12.4%, requesting 13% — delta < 2%, skip
        trades = executor.execute_decisions(response, 100000, 30000, SAMPLE_POSITIONS)

        broker.place_market_buy.assert_not_called()
        assert len(trades) == 0


class TestMixedDecisions:
    def test_close_then_new(self, executor, broker, risk, thesis_manager):
        broker.close_position.return_value = OrderResult(success=True)

        mock_plan = MagicMock()
        mock_plan.quantity = 50
        mock_plan.entry_price = 200.0
        mock_plan.catastrophic_stop = 170.0
        mock_plan.position_value = 10000.0
        mock_plan.allocation_pct = 10.0
        mock_plan.side = "LONG"
        risk.evaluate_new_position.return_value = mock_plan
        risk.is_core_position.return_value = True
        broker.place_market_buy.return_value = OrderResult(success=True)

        response = {
            "close_positions": [{"ticker": "NKE", "reason": "broken"}],
            "new_positions": [{
                "ticker": "CEG", "action": "BUY", "allocation_pct": 10,
                "direction": "LONG", "confidence": "high", "thesis": "Nuclear",
            }],
        }
        trades = executor.execute_decisions(response, 100000, 30000, SAMPLE_POSITIONS)

        assert len(trades) == 2
        assert trades[0]["ticker"] == "NKE"
        assert trades[1]["ticker"] == "CEG"


class TestFindPosition:
    def test_finds_existing(self):
        pos = _find_position(SAMPLE_POSITIONS, "NVDA")
        assert pos is not None
        assert pos["symbol"] == "NVDA"

    def test_returns_none_for_missing(self):
        assert _find_position(SAMPLE_POSITIONS, "AAPL") is None

    def test_empty_list(self):
        assert _find_position([], "NVDA") is None
