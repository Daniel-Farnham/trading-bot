from __future__ import annotations

import pytest

from src.storage.models import Signal, TradeSide
from src.strategy.risk import PositionPlan, RiskManager, RiskVeto


def _make_signal(**overrides) -> Signal:
    defaults = {
        "ticker": "AAPL",
        "side": TradeSide.BUY,
        "confidence": 0.8,
        "sentiment_score": 0.75,
        "reasoning": "Test signal",
        "current_price": 150.0,
        "stop_loss": 144.0,
        "take_profit": 159.0,
    }
    defaults.update(overrides)
    return Signal(**defaults)


class TestRiskManagerEvaluate:
    def test_approve_valid_trade(self):
        rm = RiskManager()
        signal = _make_signal()
        result = rm.evaluate(
            signal=signal,
            portfolio_value=100000.0,
            cash=80000.0,
            open_position_count=2,
            existing_ticker_positions=["MSFT"],
        )
        assert isinstance(result, PositionPlan)
        assert result.ticker == "AAPL"
        assert result.quantity > 0
        assert result.entry_price == 150.0

    def test_veto_max_positions(self):
        rm = RiskManager(params={"max_open_positions": 3})
        signal = _make_signal()
        result = rm.evaluate(
            signal=signal,
            portfolio_value=100000.0,
            cash=80000.0,
            open_position_count=3,
            existing_ticker_positions=[],
        )
        assert isinstance(result, RiskVeto)
        assert "Max open positions" in result.reason

    def test_veto_duplicate_position(self):
        rm = RiskManager()
        signal = _make_signal(ticker="AAPL")
        result = rm.evaluate(
            signal=signal,
            portfolio_value=100000.0,
            cash=80000.0,
            open_position_count=1,
            existing_ticker_positions=["AAPL"],
        )
        assert isinstance(result, RiskVeto)
        assert "Already holding" in result.reason

    def test_veto_cash_reserve(self):
        rm = RiskManager(params={
            "min_cash_reserve_pct": 0.20,
            "max_position_pct": 0.10,
            "max_open_positions": 10,
        })
        signal = _make_signal()
        result = rm.evaluate(
            signal=signal,
            portfolio_value=100000.0,
            cash=15000.0,  # Below 20% reserve of 100k
            open_position_count=0,
            existing_ticker_positions=[],
        )
        assert isinstance(result, RiskVeto)
        assert "Cash reserve" in result.reason

    def test_position_scaled_by_confidence(self):
        rm = RiskManager()

        high_conf = _make_signal(confidence=1.0)
        low_conf = _make_signal(confidence=0.3)

        result_high = rm.evaluate(
            signal=high_conf,
            portfolio_value=100000.0,
            cash=80000.0,
            open_position_count=0,
            existing_ticker_positions=[],
        )
        result_low = rm.evaluate(
            signal=low_conf,
            portfolio_value=100000.0,
            cash=80000.0,
            open_position_count=0,
            existing_ticker_positions=[],
        )

        assert isinstance(result_high, PositionPlan)
        assert isinstance(result_low, PositionPlan)
        assert result_high.quantity > result_low.quantity

    def test_position_respects_max_size(self):
        rm = RiskManager(params={
            "max_position_pct": 0.10,
            "min_cash_reserve_pct": 0.0,
            "max_open_positions": 10,
        })
        signal = _make_signal(confidence=1.0, current_price=10.0)
        result = rm.evaluate(
            signal=signal,
            portfolio_value=100000.0,
            cash=100000.0,
            open_position_count=0,
            existing_ticker_positions=[],
        )
        assert isinstance(result, PositionPlan)
        # Max 10% of 100k = 10k. At $10/share = 1000 shares max
        assert result.quantity <= 1000
        assert result.position_value <= 10000.0

    def test_risk_amount_calculated(self):
        rm = RiskManager()
        signal = _make_signal(current_price=100.0, stop_loss=95.0)
        result = rm.evaluate(
            signal=signal,
            portfolio_value=100000.0,
            cash=80000.0,
            open_position_count=0,
            existing_ticker_positions=[],
        )
        assert isinstance(result, PositionPlan)
        # Risk per share = 100 - 95 = $5
        assert result.risk_amount == result.quantity * 5.0

    def test_veto_zero_price(self):
        rm = RiskManager()
        signal = _make_signal(current_price=0.0)
        result = rm.evaluate(
            signal=signal,
            portfolio_value=100000.0,
            cash=80000.0,
            open_position_count=0,
            existing_ticker_positions=[],
        )
        assert isinstance(result, RiskVeto)


class TestDailyLossCheck:
    def test_continue_trading_small_loss(self):
        rm = RiskManager()
        assert rm.check_daily_loss(-1000.0, 100000.0) is True  # 1% < 3%

    def test_stop_trading_big_loss(self):
        rm = RiskManager()
        assert rm.check_daily_loss(-4000.0, 100000.0) is False  # 4% > 3%

    def test_continue_trading_profit(self):
        rm = RiskManager()
        assert rm.check_daily_loss(5000.0, 100000.0) is True

    def test_stop_on_zero_portfolio(self):
        rm = RiskManager()
        assert rm.check_daily_loss(-100.0, 0.0) is False


class TestDrawdownCheck:
    def test_continue_small_drawdown(self):
        rm = RiskManager()
        assert rm.check_drawdown(95000.0, 100000.0) is True  # 5% < 15%

    def test_stop_large_drawdown(self):
        rm = RiskManager()
        assert rm.check_drawdown(80000.0, 100000.0) is False  # 20% > 15%

    def test_no_drawdown(self):
        rm = RiskManager()
        assert rm.check_drawdown(100000.0, 100000.0) is True

    def test_stop_on_zero_peak(self):
        rm = RiskManager()
        assert rm.check_drawdown(50000.0, 0.0) is False
