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
        "world_view": tmp_path / "world_view.md",
        "tactical_view": tmp_path / "tactical_view.md",
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
            "world_view": tmp_path / "world_view.md",
            "journal": tmp_path / "decision_journal.md",
        }
        sim.thesis_manager._max_theses = 15
        sim.thesis_manager._max_watching = 5
        sim.thesis_manager._watching_expiry_reviews = 6
        sim.thesis_manager._watching = []
        sim.thesis_manager._max_summaries = 8
        sim.thesis_manager._max_themes = 8
        sim.thesis_manager._max_lessons = 15
        sim.thesis_manager._max_beliefs = 5
        sim.thesis_manager._max_journal_entries = 12
        sim._all_bars = {}
        sim.technicals = MagicMock()
        sim._data_dir = tmp_path
        sim.fundamentals = FundamentalsClient(cache_dir=tmp_path / "fundamentals_cache")
        sim._max_new_per_review = 3
        sim.news_client = MagicMock()
        sim.news_client.get_ticker_news.return_value = []  # No news = low risk
        sim._disable_news = True
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


class TestOptionsLedgerPersistence:
    """Options must persist in the portfolio ledger through equity trades."""

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
            "world_view": tmp_path / "world_view.md",
            "journal": tmp_path / "decision_journal.md",
        }
        sim.thesis_manager._max_theses = 15
        sim.thesis_manager._max_watching = 5
        sim.thesis_manager._watching_expiry_reviews = 6
        sim.thesis_manager._watching = []
        sim.thesis_manager._max_summaries = 8
        sim.thesis_manager._max_themes = 8
        sim.thesis_manager._max_lessons = 15
        sim.thesis_manager._max_beliefs = 5
        sim.thesis_manager._max_journal_entries = 12
        sim.thesis_manager._current_options = None
        sim._all_bars = {}
        sim.technicals = MagicMock()
        sim._data_dir = tmp_path
        sim.fundamentals = FundamentalsClient(cache_dir=tmp_path / "fundamentals_cache")
        sim._max_new_per_review = 3
        sim.news_client = MagicMock()
        sim.news_client.get_ticker_news.return_value = []
        sim._disable_news = True
        sim._atr_cache = {}
        return sim

    def test_option_survives_equity_trade_in_same_review(self, tmp_path):
        """BUY_PUT + BUY equity in same review → ledger shows both."""
        sim = self._make_sim(tmp_path)

        # Place option + equity in the same review
        response = {
            "new_positions": [
                {
                    "ticker": "NVDA",
                    "action": "BUY_PUT",
                    "allocation_pct": 2,
                    "direction": "LONG",
                    "thesis": "Insurance on concentrated position",
                    "strike_selection": "10_OTM",
                    "expiry_months": 6,
                    "confidence": "high",
                },
                {
                    "ticker": "META",
                    "action": "BUY",
                    "direction": "LONG",
                    "allocation_pct": 15,
                    "thesis": "AI advertising",
                    "confidence": "high",
                },
            ],
            "close_positions": [],
            "close_options": [],
            "reduce_positions": [],
        }
        daily_bars = {
            "NVDA": {"open": 130, "high": 135, "low": 128, "close": 132, "volume": 1000000},
            "META": {"open": 570, "high": 580, "low": 565, "close": 575, "volume": 500000},
        }
        day_dt = datetime(2024, 10, 14)

        sim._execute_decisions(response, daily_bars, day_dt)

        # Broker should have both
        assert "META" in sim.broker.positions
        assert len(sim.broker.option_positions) == 1

        # The ledger should show both equity AND options
        ledger_content = sim.thesis_manager._read("ledger")
        assert "## Equity Positions" in ledger_content
        assert "META" in ledger_content
        assert "## Option Positions" in ledger_content
        assert "NVDA" in ledger_content
        assert "PUT" in ledger_content

    def test_option_persists_after_subsequent_equity_update(self, tmp_path):
        """Option in ledger must survive a subsequent update_position call."""
        sim = self._make_sim(tmp_path)

        # First: place an option directly on the broker
        sim.broker.place_option_order(
            contract_id="NVDA_250620P120",
            ticker="NVDA", option_type="PUT", strike=120.0,
            expiry="2025-06-20", quantity=3, premium=5.00,
            is_short=False, entry_date="2024-10-14",
        )

        # Cache the options on thesis_manager (simulates what _update_ledger_values does)
        sim.thesis_manager._current_options = [{
            "ticker": "NVDA", "option_type": "PUT", "strike": 120.0,
            "expiry": "2025-06-20", "quantity": 3, "premium_paid": 5.00,
            "current_premium": 5.00, "is_short": False,
        }]

        # Now add an equity position — this calls _rebuild_ledger
        sim.thesis_manager.update_position(
            ticker="META", side="LONG", qty=20,
            entry_price=575.0, current_value=11500.0,
            date_opened="2024-10-14",
        )

        # Ledger must still contain the option
        ledger_content = sim.thesis_manager._read("ledger")
        assert "## Option Positions" in ledger_content
        assert "NVDA PUT $120" in ledger_content
        assert "META" in ledger_content

    def test_option_removed_after_close(self, tmp_path):
        """After closing an option, it should disappear from the ledger."""
        sim = self._make_sim(tmp_path)

        # Place option
        sim.broker.place_option_order(
            contract_id="NVDA_250620P120",
            ticker="NVDA", option_type="PUT", strike=120.0,
            expiry="2025-06-20", quantity=3, premium=5.00,
            is_short=False, entry_date="2024-10-14",
        )

        # Cache it
        sim.thesis_manager._current_options = [{
            "ticker": "NVDA", "option_type": "PUT", "strike": 120.0,
            "expiry": "2025-06-20", "quantity": 3, "premium_paid": 5.00,
            "current_premium": 7.00, "is_short": False,
        }]

        # Write initial ledger with option
        sim.thesis_manager.update_position(
            ticker="META", side="LONG", qty=20,
            entry_price=575.0, current_value=11500.0,
            date_opened="2024-10-14",
        )
        assert "## Option Positions" in sim.thesis_manager._read("ledger")

        # Close the option and clear cache
        sim.broker.close_option_position("NVDA_250620P120", 7.00)
        sim.thesis_manager._current_options = None

        # Rebuild ledger — option should be gone
        sim.thesis_manager.update_position(
            ticker="META", side="LONG", qty=20,
            entry_price=575.0, current_value=12000.0,
            date_opened="2024-10-14",
        )
        ledger_content = sim.thesis_manager._read("ledger")
        assert "## Option Positions" not in ledger_content
        assert "META" in ledger_content


