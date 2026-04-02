"""Tests for the SPY-based low volatility trigger and options logging."""
from __future__ import annotations

import logging

import pytest

from src.simulation.sim_broker import SimBroker
from src.simulation.thesis_sim import ThesisSimulation
from src.strategy.risk_v3 import PositionPlan


def _make_sim_with_position() -> ThesisSimulation:
    """Create a minimal ThesisSimulation with one position held."""
    sim = ThesisSimulation.__new__(ThesisSimulation)
    sim.broker = SimBroker(initial_cash=100_000.0)
    sim._spy_hv_pctl = 50.0
    sim._spy_hv_prev = 50.0
    sim._hv_cache = {}
    sim._hv_prev = {}
    sim.daily_snapshots = []

    # Need at least one position for the trigger to be relevant
    plan = PositionPlan(
        ticker="NVDA", quantity=50, entry_price=130.0,
        stop_loss=110.0, take_profit=180.0,
        risk_amount=1000.0, position_value=6500.0, risk_pct=0.065,
    )
    sim.broker.place_bracket_order(plan)
    return sim


class TestLowVolTrigger:
    def test_fires_when_spy_hv_drops_below_threshold(self):
        sim = _make_sim_with_position()
        sim._spy_hv_pctl = 20.0  # Below 30 threshold
        sim._spy_hv_prev = 45.0  # Was above threshold before

        assert sim._check_low_vol_trigger() is True

    def test_does_not_fire_when_spy_hv_above_threshold(self):
        sim = _make_sim_with_position()
        sim._spy_hv_pctl = 55.0  # Above threshold
        sim._spy_hv_prev = 60.0

        assert sim._check_low_vol_trigger() is False

    def test_debounce_prevents_repeated_firing(self):
        sim = _make_sim_with_position()
        sim._spy_hv_pctl = 20.0
        sim._spy_hv_prev = 45.0  # Was above → fires

        assert sim._check_low_vol_trigger() is True

        # Now it's already in the calm period — should NOT fire again
        sim._spy_hv_pctl = 18.0  # Still calm
        assert sim._check_low_vol_trigger() is False

    def test_resets_after_hv_goes_back_above_threshold(self):
        sim = _make_sim_with_position()

        # First calm period
        sim._spy_hv_pctl = 20.0
        sim._spy_hv_prev = 45.0
        assert sim._check_low_vol_trigger() is True

        # Vol rises back above threshold
        sim._spy_hv_pctl = 50.0
        assert sim._check_low_vol_trigger() is False

        # Vol drops again — should fire for the NEW calm period
        sim._spy_hv_pctl = 22.0
        assert sim._check_low_vol_trigger() is True

    def test_does_not_fire_with_no_positions(self):
        sim = ThesisSimulation.__new__(ThesisSimulation)
        sim.broker = SimBroker(initial_cash=100_000.0)
        sim._spy_hv_pctl = 15.0
        sim._spy_hv_prev = 50.0
        sim._hv_cache = {}
        sim._hv_prev = {}

        # No positions → no point in options review
        assert sim._check_low_vol_trigger() is False

    def test_fires_at_exactly_threshold(self):
        sim = _make_sim_with_position()
        sim._spy_hv_pctl = 30.0  # At threshold (not below)
        sim._spy_hv_prev = 45.0

        # 30 is NOT below 30 — should not fire
        assert sim._check_low_vol_trigger() is False

    def test_fires_at_just_below_threshold(self):
        sim = _make_sim_with_position()
        sim._spy_hv_pctl = 29.9
        sim._spy_hv_prev = 45.0

        assert sim._check_low_vol_trigger() is True

    def test_custom_threshold(self):
        sim = _make_sim_with_position()
        sim._spy_hv_pctl = 35.0
        sim._spy_hv_prev = 55.0

        # Default threshold (30) — 35 is not below 30, should not fire
        assert sim._check_low_vol_trigger(hv_threshold=30.0) is False

        # Reset prev to above the higher threshold
        sim._spy_hv_prev = 55.0
        # Higher threshold (40) — 35 is below 40, should fire
        assert sim._check_low_vol_trigger(hv_threshold=40.0) is True


class TestLowVolLogging:
    """Verify that low-vol trigger and option trades produce visible log output."""

    def test_low_vol_trigger_logs_message(self, caplog):
        sim = _make_sim_with_position()
        sim._spy_hv_pctl = 20.0
        sim._spy_hv_prev = 45.0

        with caplog.at_level(logging.INFO):
            sim._check_low_vol_trigger()

        assert any("LOW VOL" in record.message for record in caplog.records)
        assert any("SPY HV" in record.message for record in caplog.records)
        assert any("options premiums are cheap" in record.message for record in caplog.records)

    def test_no_log_when_not_triggered(self, caplog):
        sim = _make_sim_with_position()
        sim._spy_hv_pctl = 55.0
        sim._spy_hv_prev = 60.0

        with caplog.at_level(logging.INFO):
            sim._check_low_vol_trigger()

        assert not any("LOW VOL" in record.message for record in caplog.records)

    def test_option_trade_logs_with_prefix(self, caplog):
        """Option trades should log with 'OPTION:' prefix for visibility."""
        from datetime import datetime
        from unittest.mock import MagicMock

        sim = _make_sim_with_position()
        sim._atr_cache = {"NVDA": 4.0}
        sim._all_bars = {}
        sim.thesis_manager = MagicMock()

        new_pos = {
            "ticker": "NVDA",
            "action": "BUY_PUT",
            "allocation_pct": 2,
            "direction": "LONG",
            "strike_selection": "10_OTM",
            "expiry_months": 6,
        }
        daily_bars = {"NVDA": {"close": 130.0}}
        day_dt = datetime(2024, 10, 14)

        with caplog.at_level(logging.INFO):
            sim._execute_option_trade(new_pos, daily_bars, day_dt)

        # Should have the OPTION: prefix
        option_logs = [r.message for r in caplog.records if "OPTION:" in r.message]
        assert len(option_logs) >= 1
        assert "BOUGHT" in option_logs[0] or "SOLD" in option_logs[0]
        assert "NVDA" in option_logs[0]
        assert "PUT" in option_logs[0]
