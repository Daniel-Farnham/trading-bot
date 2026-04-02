"""Tests for options trading: broker operations, pricing, and lifecycle."""
from __future__ import annotations

import pytest

from src.simulation.sim_broker import SimBroker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _buy_call(broker: SimBroker, **overrides) -> str:
    """Place a long call and return the contract_id."""
    defaults = {
        "contract_id": "AAPL_250620C150",
        "ticker": "AAPL",
        "option_type": "CALL",
        "strike": 150.0,
        "expiry": "2025-06-20",
        "quantity": 5,
        "premium": 8.00,
        "is_short": False,
        "entry_date": "2025-01-15",
    }
    defaults.update(overrides)
    cid = defaults["contract_id"]
    result = broker.place_option_order(**defaults)
    assert result.success, f"Failed to place call: {result.error}"
    return cid


def _buy_put(broker: SimBroker, **overrides) -> str:
    """Place a long put and return the contract_id."""
    defaults = {
        "contract_id": "AAPL_250620P140",
        "ticker": "AAPL",
        "option_type": "PUT",
        "strike": 140.0,
        "expiry": "2025-06-20",
        "quantity": 3,
        "premium": 5.00,
        "is_short": False,
        "entry_date": "2025-01-15",
    }
    defaults.update(overrides)
    cid = defaults["contract_id"]
    result = broker.place_option_order(**defaults)
    assert result.success, f"Failed to place put: {result.error}"
    return cid


def _sell_put(broker: SimBroker, **overrides) -> str:
    """Sell a cash-secured put and return the contract_id."""
    defaults = {
        "contract_id": "AAPL_250620P130",
        "ticker": "AAPL",
        "option_type": "PUT",
        "strike": 130.0,
        "expiry": "2025-06-20",
        "quantity": 2,
        "premium": 4.00,
        "is_short": True,
        "entry_date": "2025-01-15",
    }
    defaults.update(overrides)
    cid = defaults["contract_id"]
    result = broker.place_option_order(**defaults)
    assert result.success, f"Failed to sell put: {result.error}"
    return cid


# ---------------------------------------------------------------------------
# Place Option Order
# ---------------------------------------------------------------------------

class TestPlaceOptionOrder:
    def test_buy_call_deducts_cash(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, quantity=5, premium=8.00)

        # 5 contracts * 100 shares * $8.00 = $4,000
        assert broker.cash == pytest.approx(96_000.0)
        assert len(broker.option_positions) == 1

    def test_buy_call_insufficient_cash(self):
        broker = SimBroker(initial_cash=1_000.0)
        result = broker.place_option_order(
            contract_id="AAPL_250620C150", ticker="AAPL", option_type="CALL",
            strike=150.0, expiry="2025-06-20", quantity=5, premium=8.00,
            is_short=False, entry_date="2025-01-15",
        )
        assert result.success is False
        assert "Insufficient cash" in result.error
        assert len(broker.option_positions) == 0

    def test_sell_put_receives_premium(self):
        broker = SimBroker(initial_cash=100_000.0)
        _sell_put(broker, quantity=2, premium=4.00, strike=130.0)

        # Receives 2 * 100 * $4.00 = $800
        assert broker.cash == pytest.approx(100_800.0)
        assert len(broker.option_positions) == 1

    def test_sell_put_insufficient_cash_for_assignment(self):
        # Strike=$500, qty=3 → needs $150,000 reserve
        broker = SimBroker(initial_cash=50_000.0)
        result = broker.place_option_order(
            contract_id="AAPL_250620P500", ticker="AAPL", option_type="PUT",
            strike=500.0, expiry="2025-06-20", quantity=3, premium=10.00,
            is_short=True, entry_date="2025-01-15",
        )
        assert result.success is False
        assert "cash-secured put" in result.error

    def test_portfolio_value_includes_long_option(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, quantity=5, premium=8.00)

        # Cash: 96,000 + Option value: 5 * 100 * 8.00 = 4,000 → total 100,000
        assert broker.portfolio_value == pytest.approx(100_000.0)
        assert broker.equity_value == pytest.approx(0.0)
        assert broker.options_value == pytest.approx(4_000.0)

    def test_portfolio_value_includes_short_option(self):
        broker = SimBroker(initial_cash=100_000.0)
        _sell_put(broker, quantity=2, premium=4.00)

        # Cash: 100,800 - Options liability: 2 * 100 * 4.00 = 800 → total 100,000
        assert broker.portfolio_value == pytest.approx(100_000.0)
        assert broker.options_value == pytest.approx(-800.0)

    def test_option_position_fields(self):
        broker = SimBroker(initial_cash=100_000.0)
        cid = _buy_call(broker, ticker="NVDA", strike=140.0, premium=12.50)

        opt = broker.option_positions[cid]
        assert opt.ticker == "NVDA"
        assert opt.option_type == "CALL"
        assert opt.strike == 140.0
        assert opt.premium_paid == 12.50
        assert opt.current_premium == 12.50  # starts at entry premium
        assert opt.is_short is False


# ---------------------------------------------------------------------------
# Close Option Position (early exit)
# ---------------------------------------------------------------------------

class TestCloseOptionPosition:
    def test_close_long_call_profit(self):
        broker = SimBroker(initial_cash=100_000.0)
        cid = _buy_call(broker, quantity=5, premium=8.00)

        result = broker.close_option_position(cid, current_premium=12.00)

        assert result.success is True
        assert cid not in broker.option_positions
        # P&L: (12 - 8) * 100 * 5 = $2,000
        assert broker.total_pnl == pytest.approx(2_000.0)
        # Cash: started 96,000 + sell at 12*100*5=6,000 = 102,000
        assert broker.cash == pytest.approx(102_000.0)

    def test_close_long_call_loss(self):
        broker = SimBroker(initial_cash=100_000.0)
        cid = _buy_call(broker, quantity=5, premium=8.00)

        broker.close_option_position(cid, current_premium=3.00)

        # P&L: (3 - 8) * 100 * 5 = -$2,500
        assert broker.total_pnl == pytest.approx(-2_500.0)
        # Cash: 96,000 + 3*100*5=1,500 = 97,500
        assert broker.cash == pytest.approx(97_500.0)

    def test_close_short_put_profit(self):
        broker = SimBroker(initial_cash=100_000.0)
        cid = _sell_put(broker, quantity=2, premium=4.00)

        # Premium decayed — buy back cheaper
        broker.close_option_position(cid, current_premium=1.50)

        # P&L: (4.00 - 1.50) * 100 * 2 = $500
        assert broker.total_pnl == pytest.approx(500.0)
        # Cash: 100,800 - 1.50*100*2=300 = 100,500
        assert broker.cash == pytest.approx(100_500.0)

    def test_close_short_put_loss(self):
        broker = SimBroker(initial_cash=100_000.0)
        cid = _sell_put(broker, quantity=2, premium=4.00)

        # Stock crashed, put premium spiked
        broker.close_option_position(cid, current_premium=15.00)

        # P&L: (4.00 - 15.00) * 100 * 2 = -$2,200
        assert broker.total_pnl == pytest.approx(-2_200.0)

    def test_close_nonexistent_option(self):
        broker = SimBroker(initial_cash=100_000.0)
        result = broker.close_option_position("FAKE_CONTRACT", 5.00)
        assert result.success is False

    def test_closed_trade_recorded(self):
        broker = SimBroker(initial_cash=100_000.0)
        cid = _buy_call(broker, quantity=5, premium=8.00)
        broker.close_option_position(cid, current_premium=12.00)

        assert len(broker.closed_trades) == 1
        trade = broker.closed_trades[0]
        assert trade["contract_id"] == cid
        assert trade["instrument"] == "OPTION"
        assert trade["option_type"] == "CALL"
        assert trade["entry_premium"] == 8.00
        assert trade["exit_premium"] == 12.00
        assert trade["pnl"] == pytest.approx(2_000.0)
        assert trade["is_short"] is False


# ---------------------------------------------------------------------------
# Option Expiry
# ---------------------------------------------------------------------------

class TestOptionExpiry:
    def test_long_call_itm_exercised(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, quantity=5, premium=8.00, strike=150.0, expiry="2025-06-20")

        expired = broker.check_option_expiry("2025-06-20", {
            "AAPL": {"close": 165.0},  # ITM by $15
        })

        assert len(expired) == 1
        assert expired[0]["exit_reason"] == "exercised"
        # P&L: (15.00 - 8.00) * 100 * 5 = $3,500
        assert expired[0]["pnl"] == pytest.approx(3_500.0)
        assert len(broker.option_positions) == 0
        # Cash: 96,000 + intrinsic 15*100*5=7,500 = 103,500
        assert broker.cash == pytest.approx(103_500.0)

    def test_long_call_otm_expires_worthless(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, quantity=5, premium=8.00, strike=150.0, expiry="2025-06-20")

        expired = broker.check_option_expiry("2025-06-20", {
            "AAPL": {"close": 140.0},  # OTM
        })

        assert len(expired) == 1
        assert expired[0]["exit_reason"] == "expired_worthless"
        # Lost full premium: -8.00 * 100 * 5 = -$4,000
        assert expired[0]["pnl"] == pytest.approx(-4_000.0)
        assert len(broker.option_positions) == 0
        # Cash unchanged from after purchase (no exercise)
        assert broker.cash == pytest.approx(96_000.0)

    def test_long_put_itm_exercised(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_put(broker, quantity=3, premium=5.00, strike=140.0, expiry="2025-06-20")

        expired = broker.check_option_expiry("2025-06-20", {
            "AAPL": {"close": 125.0},  # ITM by $15
        })

        assert len(expired) == 1
        assert expired[0]["exit_reason"] == "exercised"
        # P&L: (15.00 - 5.00) * 100 * 3 = $3,000
        assert expired[0]["pnl"] == pytest.approx(3_000.0)

    def test_short_put_itm_assigned(self):
        broker = SimBroker(initial_cash=100_000.0)
        _sell_put(broker, quantity=2, premium=4.00, strike=130.0, expiry="2025-06-20")

        expired = broker.check_option_expiry("2025-06-20", {
            "AAPL": {"close": 115.0},  # ITM by $15
        })

        assert len(expired) == 1
        assert expired[0]["exit_reason"] != "expired_worthless"
        # P&L: (4.00 - 15.00) * 100 * 2 = -$2,200
        assert expired[0]["pnl"] == pytest.approx(-2_200.0)

    def test_short_put_otm_keeps_premium(self):
        broker = SimBroker(initial_cash=100_000.0)
        _sell_put(broker, quantity=2, premium=4.00, strike=130.0, expiry="2025-06-20")

        expired = broker.check_option_expiry("2025-06-20", {
            "AAPL": {"close": 145.0},  # OTM — stock above strike
        })

        assert len(expired) == 1
        assert expired[0]["exit_reason"] == "expired_worthless"
        # Keeps full premium: 4.00 * 100 * 2 = $800
        assert expired[0]["pnl"] == pytest.approx(800.0)

    def test_not_expired_before_expiry_date(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, expiry="2025-06-20")

        expired = broker.check_option_expiry("2025-05-15", {
            "AAPL": {"close": 200.0},
        })

        assert len(expired) == 0
        assert len(broker.option_positions) == 1  # Still open

    def test_multiple_options_expiry(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, contract_id="AAPL_250620C150", strike=150.0,
                  quantity=2, premium=5.00, expiry="2025-06-20")
        _buy_put(broker, contract_id="AAPL_250620P140", strike=140.0,
                 quantity=2, premium=3.00, expiry="2025-06-20")

        expired = broker.check_option_expiry("2025-06-20", {
            "AAPL": {"close": 155.0},  # Call ITM ($5), Put OTM
        })

        assert len(expired) == 2
        assert len(broker.option_positions) == 0


# ---------------------------------------------------------------------------
# Repricing
# ---------------------------------------------------------------------------

class TestRepricing:
    def test_reprice_updates_premium(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, strike=150.0, premium=8.00, expiry="2025-06-20",
                  quantity=5)

        # Stock rallied — premium should increase
        broker.reprice_options(
            {"AAPL": {"close": 160.0}},
            current_date="2025-03-01",
        )

        opt = broker.option_positions["AAPL_250620C150"]
        # Deep ITM with time remaining — premium should be > intrinsic ($10)
        assert opt.current_premium > 10.0
        assert opt.current_premium != 8.00  # Changed from entry

    def test_reprice_at_expiry_uses_intrinsic(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, strike=150.0, premium=8.00, expiry="2025-06-20")

        broker.reprice_options(
            {"AAPL": {"close": 165.0}},
            current_date="2025-06-20",
        )

        opt = broker.option_positions["AAPL_250620C150"]
        assert opt.current_premium == pytest.approx(15.0)  # intrinsic only

    def test_reprice_otm_at_expiry_is_zero(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, strike=150.0, premium=8.00, expiry="2025-06-20")

        broker.reprice_options(
            {"AAPL": {"close": 140.0}},
            current_date="2025-06-20",
        )

        opt = broker.option_positions["AAPL_250620C150"]
        assert opt.current_premium == pytest.approx(0.0)

    def test_reprice_updates_delta(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, strike=150.0, premium=8.00, expiry="2025-06-20")

        broker.reprice_options(
            {"AAPL": {"close": 160.0}},
            current_date="2025-03-01",
        )

        opt = broker.option_positions["AAPL_250620C150"]
        # Deep ITM call delta should be close to 1.0
        assert opt.current_delta > 0.7


# ---------------------------------------------------------------------------
# Portfolio Greeks
# ---------------------------------------------------------------------------

class TestPortfolioGreeks:
    def test_empty_portfolio(self):
        broker = SimBroker(initial_cash=100_000.0)
        greeks = broker.get_portfolio_greeks()

        assert greeks["option_count"] == 0
        assert greeks["net_delta"] == 0.0
        assert greeks["total_options_value"] == 0.0

    def test_long_call_greeks(self):
        broker = SimBroker(initial_cash=100_000.0)
        _buy_call(broker, quantity=5, premium=8.00)

        greeks = broker.get_portfolio_greeks()
        assert greeks["option_count"] == 1
        assert greeks["total_options_value"] == pytest.approx(4_000.0)

    def test_short_option_negative_delta(self):
        broker = SimBroker(initial_cash=100_000.0)
        cid = _sell_put(broker, quantity=2, premium=4.00)

        # Manually set delta for testing
        broker.option_positions[cid].current_delta = 0.40

        greeks = broker.get_portfolio_greeks()
        # Short put: -1 * 0.40 * 100 * 2 = -80
        assert greeks["net_delta"] == pytest.approx(-80.0)

    def test_mixed_positions_delta(self):
        broker = SimBroker(initial_cash=100_000.0)
        call_id = _buy_call(broker, quantity=5, premium=8.00)
        put_id = _sell_put(broker, quantity=2, premium=4.00)

        # Set deltas for testing
        broker.option_positions[call_id].current_delta = 0.60
        broker.option_positions[put_id].current_delta = 0.35

        greeks = broker.get_portfolio_greeks()
        # Long call: +1 * 0.60 * 100 * 5 = +300
        # Short put: -1 * 0.35 * 100 * 2 = -70
        # Net: 230
        assert greeks["net_delta"] == pytest.approx(230.0)
        assert greeks["option_count"] == 2


# ---------------------------------------------------------------------------
# Portfolio Value Breakdown
# ---------------------------------------------------------------------------

class TestPortfolioBreakdown:
    def test_equity_and_options_separated(self):
        from src.strategy.risk_v3 import PositionPlan

        broker = SimBroker(initial_cash=100_000.0)

        # Buy some stock
        plan = PositionPlan(
            ticker="AAPL", quantity=10, entry_price=150.0,
            stop_loss=144.0, take_profit=159.0,
            risk_amount=60.0, position_value=1500.0, risk_pct=0.006,
        )
        broker.place_bracket_order(plan)

        # Buy a call
        _buy_call(broker, quantity=3, premium=6.00)

        assert broker.equity_value == pytest.approx(1_500.0)  # 10 * 150
        assert broker.options_value == pytest.approx(1_800.0)  # 3 * 100 * 6
        # Cash: 100,000 - 1,500 - 1,800 = 96,700
        assert broker.cash == pytest.approx(96_700.0)
        assert broker.portfolio_value == pytest.approx(100_000.0)


# ---------------------------------------------------------------------------
# Full Lifecycle
# ---------------------------------------------------------------------------

class TestOptionsLifecycle:
    def test_buy_reprice_close(self):
        """Buy a call, stock rallies, close for profit."""
        broker = SimBroker(initial_cash=100_000.0)
        cid = _buy_call(broker, strike=150.0, premium=8.00, quantity=5,
                        expiry="2025-06-20")

        # Stock rallies
        broker.reprice_options({"AAPL": {"close": 165.0}}, "2025-03-01")

        opt = broker.option_positions[cid]
        new_premium = opt.current_premium
        assert new_premium > 8.00  # Should have gained value

        # Close at new premium
        broker.close_option_position(cid, new_premium)

        assert len(broker.option_positions) == 0
        assert broker.total_pnl > 0
        assert len(broker.closed_trades) == 1

    def test_sell_put_expires_worthless_profit(self):
        """Sell put, stock stays above strike, keep premium."""
        broker = SimBroker(initial_cash=100_000.0)
        _sell_put(broker, strike=130.0, premium=4.00, quantity=2,
                  expiry="2025-06-20")

        initial_cash = broker.cash  # 100,800

        # Time passes, stock stays above strike
        expired = broker.check_option_expiry("2025-06-20", {
            "AAPL": {"close": 145.0},
        })

        assert len(expired) == 1
        # Full premium kept: $800
        assert broker.total_pnl == pytest.approx(800.0)
        assert len(broker.option_positions) == 0

    def test_protective_put_limits_loss(self):
        """Buy put on a stock position — put gains when stock drops."""
        from src.strategy.risk_v3 import PositionPlan

        broker = SimBroker(initial_cash=100_000.0)

        # Buy stock
        plan = PositionPlan(
            ticker="AAPL", quantity=100, entry_price=150.0,
            stop_loss=130.0, take_profit=180.0,
            risk_amount=2000.0, position_value=15000.0, risk_pct=0.02,
        )
        broker.place_bracket_order(plan)

        # Buy protective put
        put_id = _buy_put(broker, strike=145.0, premium=4.00, quantity=1,
                          expiry="2025-06-20")

        # Stock crashes to $120
        broker.update_prices({"AAPL": {"close": 120.0, "high": 150.0, "low": 119.0}})

        # Put expires ITM
        expired = broker.check_option_expiry("2025-06-20", {
            "AAPL": {"close": 120.0},
        })

        # Put P&L: (25.00 - 4.00) * 100 * 1 = $2,100 profit
        assert expired[0]["pnl"] == pytest.approx(2_100.0)
