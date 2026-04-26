"""Tests for live trade executor."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.execution.broker import OrderResult
from src.live.executor import LiveExecutor, _find_position
from src.live.portfolio_state import (
    AccountState, Performance, PortfolioSnapshot,
)


def _snapshot(*, equity=100000.0, cash=30000.0, position_count=3,
              max_positions=8, min_cash_pct=0.05) -> PortfolioSnapshot:
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
        performance=Performance(0.0, 0.0, 0.0, 0.0, "2026-04-05", 100000.0, 510.0),
        positions=[],
    )


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
        # Bracket validator now requires a coherent live price (between stop
        # and target). Without market_data on the fixture, _get_latest_price
        # would otherwise return MagicMock's default float (1.0).
        with patch.object(executor, "_get_latest_price", return_value=200.0):
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
        # Bracket validator needs a coherent live price between stop and target.
        with patch.object(executor, "_get_latest_price", return_value=100.0):
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
        broker.place_market_buy.return_value = OrderResult(
            success=True, order_id="pyramid-order-1",
        )

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


class TestCashMathValidator:
    """Direct tests for LiveExecutor._validate_cash_math()."""

    def test_user_failure_scenario_intc_plus_wdc(self, executor):
        """Reproduce the user's actual Call 3: BUY INTC 12% + BUY WDC 8% on -$24k cash."""
        snap = _snapshot(equity=108384.53, cash=-24144.61, position_count=10)
        response = {
            "new_positions": [
                {"ticker": "INTC", "action": "BUY", "allocation_pct": 12, "direction": "LONG"},
                {"ticker": "WDC",  "action": "BUY", "allocation_pct": 8,  "direction": "LONG"},
            ],
            "close_positions": [], "reduce_positions": [], "pyramid_positions": [],
        }
        ok, reason = executor._validate_cash_math(response, [], 108384.53, snap)
        assert ok is False
        assert "exceed" in reason
        assert "$21,677" in reason or "$21,676" in reason

    def test_buys_pass_when_matched_by_close(self, executor):
        snap = _snapshot(equity=108384.53, cash=-24144.61, position_count=10)
        response = {
            "new_positions": [
                {"ticker": "INTC", "action": "BUY", "allocation_pct": 12, "direction": "LONG"},
                {"ticker": "WDC",  "action": "BUY", "allocation_pct": 8,  "direction": "LONG"},
            ],
            "close_positions": [{"ticker": "MU"}],
            "reduce_positions": [], "pyramid_positions": [],
        }
        positions = [{"symbol": "MU", "market_value": 30000.0, "qty": 60, "current_price": 500.0}]
        ok, reason = executor._validate_cash_math(response, positions, 108384.53, snap)
        assert ok is True
        assert reason == ""

    def test_healthy_portfolio_single_buy_passes(self, executor):
        snap = _snapshot(equity=100000.0, cash=30000.0, position_count=3)
        response = {
            "new_positions": [
                {"ticker": "INTC", "action": "BUY", "allocation_pct": 10, "direction": "LONG"},
            ],
            "close_positions": [], "reduce_positions": [], "pyramid_positions": [],
        }
        ok, _reason = executor._validate_cash_math(response, [], 100000.0, snap)
        assert ok is True

    def test_reduce_frees_cash(self, executor):
        snap = _snapshot(equity=100000.0, cash=2000.0, position_count=3)  # only $2k - $5k reserve = $0 buying power
        # Reduce MU from 100 shares ($50k) to 50% allocation ($50k) → 0 shares freed
        # Then reduce to 5% allocation ($5k = 10 shares) → 90 shares × $500 = $45k freed
        response = {
            "new_positions": [
                {"ticker": "INTC", "action": "BUY", "allocation_pct": 30, "direction": "LONG"},
            ],
            "close_positions": [],
            "reduce_positions": [{"ticker": "MU", "new_allocation_pct": 5}],
            "pyramid_positions": [],
        }
        positions = [{"symbol": "MU", "market_value": 50000.0, "qty": 100, "current_price": 500.0}]
        ok, _ = executor._validate_cash_math(response, positions, 100000.0, snap)
        assert ok is True

    def test_options_and_shorts_excluded_from_cash_math(self, executor):
        """Options use a separate broker; shorts don't consume long cash."""
        snap = _snapshot(equity=100000.0, cash=2000.0, position_count=3)  # $0 buying power
        response = {
            "new_positions": [
                {"ticker": "NVDA", "action": "BUY_CALL", "allocation_pct": 5, "direction": "LONG"},
                {"ticker": "TSLA", "action": "BUY", "allocation_pct": 10, "direction": "SHORT"},
            ],
            "close_positions": [], "reduce_positions": [], "pyramid_positions": [],
        }
        ok, _ = executor._validate_cash_math(response, [], 100000.0, snap)
        assert ok is True

    def test_pyramid_delta_counted_not_full_target(self, executor):
        """A pyramid from 12% → 18% should consume 6% of equity, not 18%."""
        snap = _snapshot(equity=100000.0, cash=8000.0, position_count=5)  # $3k buying power
        response = {
            "new_positions": [],
            "close_positions": [], "reduce_positions": [],
            "pyramid_positions": [
                {"ticker": "MU", "new_allocation_pct": 18},  # +$6k delta
            ],
        }
        positions = [{"symbol": "MU", "market_value": 12000.0, "qty": 30, "current_price": 400.0}]
        # $6k pyramid > $3k buying power → fail
        ok, reason = executor._validate_cash_math(response, positions, 100000.0, snap)
        assert ok is False
        assert "exceed" in reason


class TestExecutorAtomicBehavior:
    """End-to-end tests for execute_decisions() with the snapshot wired in."""

    def test_over_limit_drops_all_buys(self, executor, broker, risk):
        """When over the position cap, NO buys/pyramids may execute."""
        snap = _snapshot(equity=100000.0, cash=30000.0, position_count=10, max_positions=8)
        response = {
            "new_positions": [
                {"ticker": "INTC", "action": "BUY", "allocation_pct": 5,
                 "direction": "LONG", "confidence": "medium", "thesis": "x",
                 "stop_price": 30, "target_price": 60},
            ],
            "close_positions": [], "reduce_positions": [], "pyramid_positions": [],
        }
        with patch.object(executor, "_get_latest_price", return_value=40.0):
            trades = executor.execute_decisions(
                response=response, portfolio_value=100000, cash=30000,
                positions=[], snapshot=snap,
            )
        assert trades == []
        broker.place_market_buy.assert_not_called()
        broker.place_bracket_order.assert_not_called()

    def test_failed_cash_math_drops_buys_but_keeps_closes(self, executor, broker, risk, thesis_manager):
        """Closes still execute when cash math fails — frees cash for next call."""
        snap = _snapshot(equity=100000.0, cash=2000.0, position_count=5)  # $0 buying power
        broker.close_position.return_value = OrderResult(success=True)

        # Close NVDA frees only $12,400; buy is 25% = $25,000 → math fails by ~$12.6k
        response = {
            "close_positions": [{"ticker": "NVDA", "reason": "thesis broken"}],
            "reduce_positions": [],
            "new_positions": [
                {"ticker": "INTC", "action": "BUY", "allocation_pct": 25,
                 "direction": "LONG", "confidence": "medium", "thesis": "x",
                 "stop_price": 30, "target_price": 60},
            ],
            "pyramid_positions": [],
        }
        positions = [
            {"symbol": "NVDA", "qty": "80", "avg_entry_price": "125.00",
             "current_price": "155.00", "market_value": 12400.00,
             "unrealized_pl": "0", "unrealized_plpc": "0"},
        ]
        with patch.object(executor, "_get_latest_price", return_value=40.0):
            trades = executor.execute_decisions(
                response=response, portfolio_value=100000, cash=2000,
                positions=positions, snapshot=snap,
            )

        # Close happened
        broker.close_position.assert_called_once_with("NVDA")
        assert any(t["action"] == "CLOSE" for t in trades)
        # Buy did NOT happen
        broker.place_bracket_order.assert_not_called()
        broker.place_market_buy.assert_not_called()
        assert not any(t["action"].startswith("BUY") for t in trades)

    def test_running_cash_decremented_between_buys(self, executor, broker, risk):
        """Two BUYs in one response: second sees decremented cash, not original."""
        # $20k cash, $5k reserve → $15k buying power; two 5% buys = $10k, fits.
        snap = _snapshot(equity=100000.0, cash=20000.0, position_count=2)
        # Risk manager passes through; we'll capture the cash arg per call
        observed_cash = []

        def evaluate(*args, **kwargs):
            observed_cash.append(kwargs.get("cash"))
            plan = MagicMock()
            plan.quantity = 50
            plan.entry_price = 100.0
            plan.catastrophic_stop = 85.0
            plan.position_value = 5000.0  # each buy uses $5k
            plan.allocation_pct = 5.0
            plan.side = "LONG"
            return plan

        risk.evaluate_new_position.side_effect = evaluate
        risk.is_core_position.return_value = True
        broker.place_market_buy.return_value = OrderResult(success=True, order_id="x")

        response = {
            "new_positions": [
                {"ticker": "AAA", "action": "BUY", "allocation_pct": 5,
                 "direction": "LONG", "confidence": "high", "thesis": "x"},
                {"ticker": "BBB", "action": "BUY", "allocation_pct": 5,
                 "direction": "LONG", "confidence": "high", "thesis": "x"},
            ],
            "close_positions": [], "reduce_positions": [], "pyramid_positions": [],
        }
        with patch.object(executor, "_get_latest_price", return_value=100.0):
            executor.execute_decisions(
                response=response, portfolio_value=100000, cash=20000,
                positions=[], snapshot=snap,
            )

        assert len(observed_cash) == 2
        assert observed_cash[0] == 20000  # first buy sees original cash
        assert observed_cash[1] == 20000 - 5000  # second buy sees cash minus first trade's value

    def test_back_compat_no_snapshot_still_works(self, executor, broker, risk):
        """Callers that don't pass snapshot (legacy path) skip the validator entirely."""
        risk.evaluate_new_position.return_value = MagicMock(
            quantity=10, entry_price=100.0, catastrophic_stop=85.0,
            position_value=1000.0, allocation_pct=5.0, side="LONG",
        )
        risk.is_core_position.return_value = True
        broker.place_market_buy.return_value = OrderResult(success=True, order_id="y")

        response = {
            "new_positions": [{
                "ticker": "AAA", "action": "BUY", "allocation_pct": 5,
                "direction": "LONG", "confidence": "high", "thesis": "x",
            }],
            "close_positions": [], "reduce_positions": [], "pyramid_positions": [],
        }
        with patch.object(executor, "_get_latest_price", return_value=100.0):
            trades = executor.execute_decisions(
                response=response, portfolio_value=100000, cash=30000,
                positions=[],
                # snapshot omitted on purpose
            )
        assert len(trades) == 1


class TestFindPosition:
    def test_finds_existing(self):
        pos = _find_position(SAMPLE_POSITIONS, "NVDA")
        assert pos is not None
        assert pos["symbol"] == "NVDA"

    def test_returns_none_for_missing(self):
        assert _find_position(SAMPLE_POSITIONS, "AAPL") is None

    def test_empty_list(self):
        assert _find_position([], "NVDA") is None
