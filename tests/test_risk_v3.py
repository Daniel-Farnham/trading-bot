from __future__ import annotations

import pytest

from src.strategy.risk_v3 import RiskManagerV3, V3PositionPlan, V3RiskVeto


@pytest.fixture
def risk():
    return RiskManagerV3(params={
        "max_positions": 15,
        "max_single_position_pct": 0.10,
        "min_cash_reserve_pct": 0.20,
        "catastrophic_stop_pct": 0.18,
        "max_short_exposure_pct": 0.20,
        "max_drawdown_pct": 0.25,
    })


class TestEvaluateNewPosition:
    def test_basic_long(self, risk):
        result = risk.evaluate_new_position(
            ticker="NVDA", side="LONG", allocation_pct=6,
            price=800.0, portfolio_value=100000, cash=80000,
            open_position_count=0, existing_tickers=[],
        )
        assert isinstance(result, V3PositionPlan)
        assert result.ticker == "NVDA"
        assert result.side == "LONG"
        assert result.quantity > 0
        assert result.allocation_pct == 6.0
        # Catastrophic stop should be 18% below entry
        assert result.catastrophic_stop == round(800 * 0.82, 2)

    def test_basic_short(self, risk):
        result = risk.evaluate_new_position(
            ticker="TSLA", side="SHORT", allocation_pct=5,
            price=200.0, portfolio_value=100000, cash=80000,
            open_position_count=0, existing_tickers=[],
        )
        assert isinstance(result, V3PositionPlan)
        assert result.side == "SHORT"
        # Catastrophic stop should be 18% above entry
        assert result.catastrophic_stop == round(200 * 1.18, 2)

    def test_caps_at_confidence_tier(self, risk):
        # Medium confidence caps at 8%
        result = risk.evaluate_new_position(
            ticker="AAPL", side="LONG", allocation_pct=15,
            price=150.0, portfolio_value=100000, cash=80000,
            open_position_count=0, existing_tickers=[], confidence="medium",
        )
        assert isinstance(result, V3PositionPlan)
        assert result.allocation_pct == 8.0  # Capped at medium tier

    def test_highest_confidence_allows_15pct(self, risk):
        result = risk.evaluate_new_position(
            ticker="AAPL", side="LONG", allocation_pct=15,
            price=150.0, portfolio_value=100000, cash=80000,
            open_position_count=0, existing_tickers=[], confidence="highest",
        )
        assert isinstance(result, V3PositionPlan)
        assert result.allocation_pct == 15.0

    def test_high_confidence_caps_at_10pct(self, risk):
        result = risk.evaluate_new_position(
            ticker="AAPL", side="LONG", allocation_pct=12,
            price=150.0, portfolio_value=100000, cash=80000,
            open_position_count=0, existing_tickers=[], confidence="high",
        )
        assert isinstance(result, V3PositionPlan)
        assert result.allocation_pct == 10.0

    def test_veto_max_positions(self, risk):
        result = risk.evaluate_new_position(
            ticker="AAPL", side="LONG", allocation_pct=6,
            price=150.0, portfolio_value=100000, cash=80000,
            open_position_count=15, existing_tickers=[],
        )
        assert isinstance(result, V3RiskVeto)
        assert "Max positions" in result.reason

    def test_veto_duplicate_ticker(self, risk):
        result = risk.evaluate_new_position(
            ticker="NVDA", side="LONG", allocation_pct=6,
            price=800.0, portfolio_value=100000, cash=80000,
            open_position_count=1, existing_tickers=["NVDA"],
        )
        assert isinstance(result, V3RiskVeto)
        assert "Already holding" in result.reason

    def test_veto_cash_reserve(self, risk):
        # Cash is exactly at 20% reserve — no room
        result = risk.evaluate_new_position(
            ticker="AAPL", side="LONG", allocation_pct=6,
            price=150.0, portfolio_value=100000, cash=20000,
            open_position_count=0, existing_tickers=[],
        )
        assert isinstance(result, V3RiskVeto)
        assert "Cash reserve" in result.reason

    def test_veto_short_exposure(self, risk):
        result = risk.evaluate_new_position(
            ticker="TSLA", side="SHORT", allocation_pct=5,
            price=200.0, portfolio_value=100000, cash=80000,
            open_position_count=0, existing_tickers=[],
            short_exposure=20000,  # Already at 20% max
        )
        assert isinstance(result, V3RiskVeto)
        assert "short exposure" in result.reason

    def test_limited_by_available_cash(self, risk):
        # Only $25k cash, $20k reserved → $5k available, but wants 6% = $6k
        result = risk.evaluate_new_position(
            ticker="AAPL", side="LONG", allocation_pct=6,
            price=150.0, portfolio_value=100000, cash=25000,
            open_position_count=0, existing_tickers=[],
        )
        assert isinstance(result, V3PositionPlan)
        # Should be limited to what cash allows
        assert result.position_value <= 5000


class TestEvaluateReduce:
    def test_reduce_position(self, risk):
        shares = risk.evaluate_reduce(
            ticker="NVDA", new_allocation_pct=3,
            current_qty=10, price=800.0, portfolio_value=100000,
        )
        # 3% of $100k = $3k, at $800/share = 3 shares, so sell 7
        assert shares == 7

    def test_no_reduction_needed(self, risk):
        shares = risk.evaluate_reduce(
            ticker="AAPL", new_allocation_pct=10,
            current_qty=5, price=150.0, portfolio_value=100000,
        )
        # 10% of $100k = $10k, at $150/share = 66 shares — more than 5
        assert shares == 0


class TestDrawdown:
    def test_within_limit(self, risk):
        assert risk.check_drawdown(90000, 100000) is True

    def test_exceeded(self, risk):
        # Default V3 is 25%
        assert risk.check_drawdown(74000, 100000) is False

    def test_at_boundary(self, risk):
        assert risk.check_drawdown(76000, 100000) is True
