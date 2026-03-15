"""Tests for the V3 thesis simulation engine.

Focuses on the orchestration logic — decision execution, stop checks,
ledger updates. Claude calls and market data are mocked.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.research.fundamentals import FundamentalsClient
from src.simulation.sim_broker import SimBroker
from src.simulation.thesis_sim import ThesisSimulation
from src.strategy.risk_v3 import RiskManagerV3, V3PositionPlan, V3RiskVeto
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
    }
    mgr._max_theses = 15
    mgr._max_summaries = 8
    mgr._max_themes = 8
    mgr._max_lessons = 15
    mgr._max_beliefs = 5
    return mgr


@pytest.fixture
def broker():
    return SimBroker(initial_cash=100000.0)


class TestExecuteDecisions:
    """Test the _execute_decisions method in isolation."""

    def _make_sim(self, tmp_path):
        """Create a ThesisSimulation with mocked dependencies."""
        sim = ThesisSimulation.__new__(ThesisSimulation)
        sim.broker = SimBroker(initial_cash=100000.0)
        sim.risk = RiskManagerV3(params={
            "max_positions": 15,
            "max_single_position_pct": 0.10,
            "min_cash_reserve_pct": 0.20,
            "catastrophic_stop_pct": 0.18,
            "max_short_exposure_pct": 0.20,
        })
        sim.thesis_manager = ThesisManager.__new__(ThesisManager)
        sim.thesis_manager._paths = {
            "theses": tmp_path / "active_theses.md",
            "ledger": tmp_path / "portfolio_ledger.md",
            "summaries": tmp_path / "quarterly_summaries.md",
            "lessons": tmp_path / "lessons_learned.md",
            "themes": tmp_path / "themes.md",
            "beliefs": tmp_path / "beliefs.md",
        }
        sim.thesis_manager._max_theses = 15
        sim.thesis_manager._max_summaries = 8
        sim.thesis_manager._max_themes = 8
        sim.thesis_manager._max_lessons = 15
        sim.thesis_manager._max_beliefs = 5
        sim._all_bars = {}
        sim.technicals = MagicMock()
        sim._data_dir = tmp_path
        sim.fundamentals = FundamentalsClient(cache_dir=tmp_path / "fundamentals_cache")
        return sim

    def test_buy_new_position(self, tmp_path):
        sim = self._make_sim(tmp_path)
        response = {
            "new_positions": [{
                "ticker": "NVDA",
                "direction": "LONG",
                "allocation_pct": 6,
                "thesis": "AI demand",
            }],
            "close_positions": [],
            "reduce_positions": [],
        }
        daily_bars = {"NVDA": {"open": 800, "high": 810, "low": 795, "close": 805, "volume": 1000000}}
        day_dt = datetime(2024, 1, 15)

        sim._execute_decisions(response, daily_bars, day_dt)

        assert "NVDA" in sim.broker.positions
        pos = sim.broker.positions["NVDA"]
        assert pos.entry_price == 805.0
        assert pos.quantity > 0

        # Memory should be updated
        holdings = sim.thesis_manager.get_holdings()
        assert len(holdings) == 1
        assert holdings[0]["ticker"] == "NVDA"

    def test_close_position(self, tmp_path):
        sim = self._make_sim(tmp_path)

        # Open a position first
        from src.strategy.risk_v3 import PositionPlan
        plan = PositionPlan(
            ticker="TSLA", quantity=10, entry_price=200.0,
            stop_loss=164.0, take_profit=400.0,
            risk_amount=360, position_value=2000, risk_pct=0.036,
        )
        sim.broker.place_bracket_order(plan, opened_at="2024-01-10")
        sim.thesis_manager.update_position("TSLA", "LONG", 10, 200.0, 2000.0, "2024-01-10")

        response = {
            "new_positions": [],
            "close_positions": [{"ticker": "TSLA", "reason": "Thesis broken"}],
            "reduce_positions": [],
        }
        daily_bars = {"TSLA": {"open": 190, "high": 195, "low": 185, "close": 190, "volume": 500000}}

        sim._execute_decisions(response, daily_bars, datetime(2024, 1, 20))

        assert "TSLA" not in sim.broker.positions
        assert len(sim.broker.closed_trades) == 1
        # Ledger should be updated
        assert sim.thesis_manager.get_holdings() == []

    def test_veto_skips_position(self, tmp_path):
        sim = self._make_sim(tmp_path)
        # Simulate a nearly fully invested portfolio: $100k portfolio, only $15k cash
        # The risk manager checks cash reserve against portfolio_value (broker.portfolio_value)
        # With $15k cash and no positions, portfolio_value = $15k
        # 20% reserve of $15k = $3k, leaving $12k available — still enough.
        # To truly veto, we need cash below reserve threshold.
        # Set cash to $100 — portfolio = $100, reserve = $20, available = $80 — too small for 1 share
        sim.broker.cash = 100  # Portfolio = $100, reserve = $20, available = $80

        response = {
            "new_positions": [{
                "ticker": "AAPL",
                "direction": "LONG",
                "allocation_pct": 6,
                "thesis": "Ecosystem growth",
            }],
            "close_positions": [],
            "reduce_positions": [],
        }
        daily_bars = {"AAPL": {"open": 150, "high": 155, "low": 148, "close": 152, "volume": 800000}}

        sim._execute_decisions(response, daily_bars, datetime(2024, 1, 15))

        assert "AAPL" not in sim.broker.positions


class TestCatastrophicStops:
    def test_stop_triggers(self, tmp_path):
        sim = TestExecuteDecisions()._make_sim(tmp_path)

        from src.strategy.risk_v3 import PositionPlan
        plan = PositionPlan(
            ticker="NVDA", quantity=5, entry_price=800.0,
            stop_loss=656.0,  # 18% below
            take_profit=1600.0,
            risk_amount=720, position_value=4000, risk_pct=0.072,
        )
        sim.broker.place_bracket_order(plan, opened_at="2024-01-10")
        sim.thesis_manager.update_position("NVDA", "LONG", 5, 800.0, 4000.0, "2024-01-10")
        sim.thesis_manager.add_thesis("NVDA", "LONG", "AI", 800, 1000, 656)

        # Price drops below stop
        daily_bars = {"NVDA": {"open": 660, "high": 665, "low": 640, "close": 650, "volume": 2000000}}
        sim._check_catastrophic_stops(daily_bars, datetime(2024, 2, 1))

        assert "NVDA" not in sim.broker.positions
        assert len(sim.broker.closed_trades) == 1
        assert sim.thesis_manager.get_holdings() == []


