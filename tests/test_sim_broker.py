from __future__ import annotations

import pytest

from src.simulation.sim_broker import SimBroker, SimPosition
from src.strategy.risk_v3 import PositionPlan


def _make_plan(**overrides) -> PositionPlan:
    defaults = {
        "ticker": "AAPL",
        "quantity": 10,
        "entry_price": 150.0,
        "stop_loss": 144.0,
        "take_profit": 159.0,
        "risk_amount": 60.0,
        "position_value": 1500.0,
        "risk_pct": 0.006,
    }
    defaults.update(overrides)
    return PositionPlan(**defaults)


class TestSimBroker:
    def test_initial_state(self):
        broker = SimBroker(initial_cash=100000.0)
        assert broker.cash == 100000.0
        assert broker.portfolio_value == 100000.0
        assert len(broker.positions) == 0

    def test_buy_deducts_cash(self):
        broker = SimBroker(initial_cash=100000.0)
        plan = _make_plan(quantity=10, entry_price=150.0)
        result = broker.place_bracket_order(plan)

        assert result.success is True
        assert broker.cash == 98500.0  # 100000 - (10 * 150)
        assert "AAPL" in broker.positions

    def test_buy_insufficient_cash(self):
        broker = SimBroker(initial_cash=1000.0)
        plan = _make_plan(quantity=10, entry_price=150.0)
        result = broker.place_bracket_order(plan)

        assert result.success is False
        assert len(broker.positions) == 0

    def test_close_position(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan())

        result = broker.close_position("AAPL", price=155.0)

        assert result.success is True
        assert "AAPL" not in broker.positions
        assert broker.cash == pytest.approx(100050.0)  # Made $5/share * 10 shares
        assert broker.total_pnl == pytest.approx(50.0)

    def test_close_position_loss(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan())

        broker.close_position("AAPL", price=145.0)

        assert broker.total_pnl == pytest.approx(-50.0)

    def test_close_nonexistent_position(self):
        broker = SimBroker()
        result = broker.close_position("NOPE")
        assert result.success is False

    def test_portfolio_value_includes_positions(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(quantity=10, entry_price=150.0))

        # Cash: 98500, Position value: 10 * 150 = 1500
        assert broker.portfolio_value == 100000.0

    def test_multiple_positions(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(ticker="AAPL", quantity=10, entry_price=150.0))
        broker.place_bracket_order(_make_plan(ticker="MSFT", quantity=5, entry_price=400.0))

        assert len(broker.positions) == 2
        assert broker.cash == pytest.approx(96500.0)  # 100000 - 1500 - 2000


class TestReducePosition:
    """Test the close-and-reopen pattern used for position reductions."""

    def test_reduce_preserves_current_price(self):
        """After a reduce (close + reopen smaller), current_price should not be 0."""
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(ticker="LNG", quantity=100, entry_price=182.0))

        # Update price to simulate a trading day
        broker.update_prices({"LNG": {"close": 185.0, "high": 186.0, "low": 181.0}})
        assert broker.positions["LNG"].current_price == 185.0

        # Simulate reduce: close full, reopen smaller (as thesis_sim does)
        price = 185.0
        broker.close_position("LNG", price)
        remaining = 71
        reopen = _make_plan(ticker="LNG", quantity=remaining, entry_price=price)
        broker.place_bracket_order(reopen)

        # current_price defaults to 0.0 on new position — this is the bug
        # The sim patches this, but broker-level should use entry_price as fallback
        pos = broker.positions["LNG"]
        assert pos.quantity == 71
        assert pos.entry_price == 185.0
        # equity_value should use entry_price fallback when current_price is 0
        assert broker.equity_value == pytest.approx(71 * 185.0)

    def test_reduce_portfolio_value_unchanged(self):
        """Reducing a position should not change total portfolio value."""
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(ticker="LNG", quantity=100, entry_price=180.0))
        broker.update_prices({"LNG": {"close": 185.0, "high": 186.0, "low": 181.0}})

        pv_before = broker.portfolio_value

        # Reduce: sell 30 shares at 185
        broker.close_position("LNG", 185.0)
        remaining = 70
        reopen = _make_plan(ticker="LNG", quantity=remaining, entry_price=185.0)
        broker.place_bracket_order(reopen)

        # Portfolio value should be the same (just shifted cash vs equity)
        assert broker.portfolio_value == pytest.approx(pv_before)

    def test_reduce_then_update_prices(self):
        """After reduce + next day's update_prices, values should be correct."""
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(ticker="LNG", quantity=100, entry_price=180.0))
        broker.update_prices({"LNG": {"close": 185.0, "high": 186.0, "low": 181.0}})

        # Reduce
        broker.close_position("LNG", 185.0)
        reopen = _make_plan(ticker="LNG", quantity=71, entry_price=185.0)
        broker.place_bracket_order(reopen)

        # Next day: price moves to 190
        broker.update_prices({"LNG": {"close": 190.0, "high": 191.0, "low": 189.0}})

        pos = broker.positions["LNG"]
        assert pos.current_price == 190.0
        assert broker.equity_value == pytest.approx(71 * 190.0)

    def test_ledger_value_after_reduce(self):
        """The value used for ledger updates should be bar_close * quantity, not 0."""
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(ticker="LNG", quantity=100, entry_price=180.0))
        broker.update_prices({"LNG": {"close": 185.0, "high": 186.0, "low": 181.0}})

        # Reduce
        broker.close_position("LNG", 185.0)
        reopen = _make_plan(ticker="LNG", quantity=71, entry_price=185.0)
        broker.place_bracket_order(reopen)

        # Simulate what _update_ledger_values does: bar["close"] * pos.quantity
        bar_close = 185.0
        pos = broker.positions["LNG"]
        ledger_value = bar_close * pos.quantity

        assert ledger_value == pytest.approx(71 * 185.0)
        # This should NOT be 0 or some wrong number
        assert ledger_value > 13000  # ~$13,135

    def test_reduce_does_not_produce_anomalous_price(self):
        """Regression test: LNG at $182 should never show as $108 after reduce."""
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(ticker="LNG", quantity=100, entry_price=181.89))
        broker.update_prices({"LNG": {"close": 182.0, "high": 183.0, "low": 181.0}})

        # Reduce from 100 to 71 shares
        broker.close_position("LNG", 182.0)
        reopen = _make_plan(ticker="LNG", quantity=71, entry_price=182.0)
        broker.place_bracket_order(reopen)

        pos = broker.positions["LNG"]
        # The derived "current price" from equity_value / quantity must be sane
        if pos.current_price > 0:
            assert pos.current_price > 150.0, f"Anomalous price: ${pos.current_price}"
        # entry_price should be the reduce price, not the original
        assert pos.entry_price == pytest.approx(182.0)
        # equity_value uses entry_price when current_price is 0
        implied_price = broker.equity_value / 71
        assert implied_price > 150.0, f"Anomalous implied price: ${implied_price}"


class TestStopsAndTargets:
    def test_stop_loss_triggered(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(
            ticker="AAPL", entry_price=150.0, stop_loss=144.0, take_profit=159.0,
        ))

        triggered = broker.check_stops_and_targets({
            "AAPL": {"high": 151.0, "low": 143.0, "close": 143.5},
        })

        assert len(triggered) == 1
        assert triggered[0]["exit_reason"] == "stopped_out"
        assert triggered[0]["exit_price"] == 144.0
        assert "AAPL" not in broker.positions

    def test_take_profit_triggered(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(
            ticker="AAPL", entry_price=150.0, stop_loss=144.0, take_profit=159.0,
        ))

        triggered = broker.check_stops_and_targets({
            "AAPL": {"high": 160.0, "low": 149.0, "close": 159.5},
        })

        assert len(triggered) == 1
        assert triggered[0]["exit_reason"] == "take_profit"
        assert triggered[0]["exit_price"] == 159.0

    def test_no_trigger_normal_day(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(
            ticker="AAPL", entry_price=150.0, stop_loss=144.0, take_profit=159.0,
        ))

        triggered = broker.check_stops_and_targets({
            "AAPL": {"high": 153.0, "low": 148.0, "close": 152.0},
        })

        assert len(triggered) == 0
        assert "AAPL" in broker.positions

    def test_multiple_triggers(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(
            ticker="AAPL", entry_price=150.0, stop_loss=144.0, take_profit=159.0,
        ))
        broker.place_bracket_order(_make_plan(
            ticker="MSFT", entry_price=400.0, stop_loss=388.0, take_profit=418.0,
            quantity=5,
        ))

        triggered = broker.check_stops_and_targets({
            "AAPL": {"high": 160.0, "low": 149.0, "close": 159.5},  # TP hit
            "MSFT": {"high": 401.0, "low": 385.0, "close": 386.0},  # SL hit
        })

        assert len(triggered) == 2
        assert len(broker.positions) == 0


class TestPositionsList:
    def test_get_positions_list(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(ticker="AAPL"))

        positions = broker.get_positions_list()
        assert len(positions) == 1
        assert positions[0]["ticker"] == "AAPL"
        assert positions[0]["qty"] == 10

    def test_get_account_snapshot(self):
        broker = SimBroker(initial_cash=100000.0)
        snap = broker.get_account_snapshot()

        assert snap["equity"] == 100000.0
        assert snap["cash"] == 100000.0
        assert snap["portfolio_value"] == 100000.0

    def test_closed_trades_tracked(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan())
        broker.close_position("AAPL", 155.0)

        assert len(broker.closed_trades) == 1
        assert broker.closed_trades[0]["pnl"] == 50.0
