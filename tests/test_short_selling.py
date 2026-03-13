"""Tests for short selling across SimBroker, signals, and risk."""
from __future__ import annotations

import pytest

from src.analysis.technical import TechnicalSnapshot
from src.simulation.sim_broker import SimBroker, SimPosition
from src.storage.models import Signal, SentimentRecord, TradeSide
from src.strategy.risk import PositionPlan, RiskManager, RiskVeto
from src.strategy.signals import SignalContext, SignalGenerator


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
        "is_short": False,
    }
    defaults.update(overrides)
    return PositionPlan(**defaults)


def _make_technicals(**overrides) -> TechnicalSnapshot:
    defaults = {
        "ticker": "AAPL",
        "rsi_14": 50.0,
        "sma_20": 150.0,
        "sma_50": 145.0,
        "atr_14": 3.0,
        "current_price": 155.0,
        "avg_volume_20": 1e6,
        "latest_volume": 1.5e6,
    }
    defaults.update(overrides)
    return TechnicalSnapshot(**defaults)


class TestSimBrokerShort:
    def test_short_position_opens(self):
        broker = SimBroker(initial_cash=100000.0)
        plan = _make_plan(quantity=10, entry_price=150.0)
        result = broker.place_bracket_order(plan, is_short=True)

        assert result.success is True
        assert "AAPL" in broker.positions
        assert broker.positions["AAPL"].is_short is True
        # Short: we receive cash from selling
        assert broker.cash == 101500.0  # 100000 + (10 * 150)

    def test_short_close_profit(self):
        """Short at 150, close at 140 = $100 profit."""
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(entry_price=150.0, quantity=10), is_short=True)

        result = broker.close_position("AAPL", price=140.0)

        assert result.success is True
        assert "AAPL" not in broker.positions
        assert broker.total_pnl == pytest.approx(100.0)  # (150-140) * 10

    def test_short_close_loss(self):
        """Short at 150, close at 160 = $100 loss."""
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(entry_price=150.0, quantity=10), is_short=True)

        broker.close_position("AAPL", price=160.0)

        assert broker.total_pnl == pytest.approx(-100.0)  # (150-160) * 10

    def test_short_stop_loss_triggered(self):
        """Short stop-loss is ABOVE entry — triggered when price goes UP."""
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(
            _make_plan(entry_price=150.0, stop_loss=156.0, take_profit=141.0, quantity=10),
            is_short=True,
        )

        triggered = broker.check_stops_and_targets({
            "AAPL": {"high": 157.0, "low": 149.0, "close": 156.5},
        })

        assert len(triggered) == 1
        assert triggered[0]["exit_reason"] == "stopped_out"
        assert triggered[0]["exit_price"] == 156.0
        assert triggered[0]["pnl"] < 0  # Loss on short when price goes up

    def test_short_take_profit_triggered(self):
        """Short take-profit is BELOW entry — triggered when price goes DOWN."""
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(
            _make_plan(entry_price=150.0, stop_loss=156.0, take_profit=141.0, quantity=10),
            is_short=True,
        )

        triggered = broker.check_stops_and_targets({
            "AAPL": {"high": 151.0, "low": 140.0, "close": 140.5},
        })

        assert len(triggered) == 1
        assert triggered[0]["exit_reason"] == "take_profit"
        assert triggered[0]["exit_price"] == 141.0
        assert triggered[0]["pnl"] > 0  # Profit when price drops

    def test_short_exposure(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(entry_price=150.0, quantity=10), is_short=True)
        broker.place_bracket_order(
            _make_plan(ticker="MSFT", entry_price=400.0, quantity=5), is_short=True,
        )

        assert broker.get_short_exposure() == pytest.approx(3500.0)  # 1500 + 2000

    def test_mixed_long_short_positions(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(ticker="AAPL", entry_price=150.0, quantity=10))
        broker.place_bracket_order(
            _make_plan(ticker="TSLA", entry_price=200.0, quantity=5), is_short=True,
        )

        assert len(broker.positions) == 2
        assert broker.positions["AAPL"].is_short is False
        assert broker.positions["TSLA"].is_short is True

    def test_closed_trade_records_short_flag(self):
        broker = SimBroker(initial_cash=100000.0)
        broker.place_bracket_order(_make_plan(entry_price=150.0, quantity=10), is_short=True)
        broker.close_position("AAPL", 140.0)

        assert len(broker.closed_trades) == 1
        assert broker.closed_trades[0]["is_short"] is True


class TestShortSignals:
    def test_short_signal_generated(self):
        gen = SignalGenerator(params={
            "enable_short_selling": True,
            "short_sentiment_threshold": -0.6,
            "sentiment_buy_threshold": 0.6,
            "sentiment_sell_threshold": -0.4,
            "atr_stop_loss_multiplier": 2.0,
            "atr_take_profit_multiplier": 3.0,
            "theme_nudge_strength": 0.20,
        })

        technicals = _make_technicals(
            rsi_14=75.0,  # Overbought
            sma_20=95.0, sma_50=100.0,  # Downtrend
            macd_histogram=-0.5,  # MACD bearish
        )
        records = [SentimentRecord("AAPL", "Bad news", "test", -0.8, "2025-01-01")]
        ctx = SignalContext(
            ticker="AAPL",
            sentiment_records=records,
            technicals=technicals,
            avg_sentiment=-0.8,
        )

        signal = gen.evaluate(ctx)

        assert signal is not None
        assert signal.side == TradeSide.SELL
        # Short signal has stop ABOVE entry, take-profit BELOW
        assert signal.stop_loss > signal.current_price
        assert signal.take_profit < signal.current_price
        assert "SHORT" in signal.reasoning

    def test_no_short_when_disabled(self):
        gen = SignalGenerator(params={
            "enable_short_selling": False,
            "sentiment_buy_threshold": 0.6,
            "sentiment_sell_threshold": -0.4,
        })

        technicals = _make_technicals(rsi_14=75.0, sma_20=95.0, sma_50=100.0)
        records = [SentimentRecord("AAPL", "Bad news", "test", -0.8, "2025-01-01")]
        ctx = SignalContext(
            ticker="AAPL",
            sentiment_records=records,
            technicals=technicals,
            avg_sentiment=-0.8,
        )

        signal = gen.evaluate(ctx)

        # Should still generate a sell-exit signal, not a short
        if signal is not None:
            assert signal.stop_loss == 0.0  # Sell-exit, not short

    def test_no_short_on_weak_negative_sentiment(self):
        gen = SignalGenerator(params={
            "enable_short_selling": True,
            "short_sentiment_threshold": -0.6,
            "sentiment_buy_threshold": 0.6,
            "sentiment_sell_threshold": -0.4,
        })

        technicals = _make_technicals(rsi_14=75.0)
        records = [SentimentRecord("AAPL", "Meh news", "test", -0.3, "2025-01-01")]
        ctx = SignalContext(
            ticker="AAPL",
            sentiment_records=records,
            technicals=technicals,
            avg_sentiment=-0.3,
        )

        signal = gen.evaluate(ctx)
        # -0.3 is above -0.6 threshold, so no short
        # Also above -0.4 sell threshold, so no sell either
        assert signal is None


class TestRiskManagerShort:
    def test_short_position_plan(self):
        rm = RiskManager(params={
            "max_open_positions": 10,
            "min_cash_reserve_pct": 0.20,
            "max_position_pct": 0.10,
            "max_short_exposure_pct": 0.30,
            "max_short_position_pct": 0.10,
        })

        signal = Signal(
            ticker="AAPL", side=TradeSide.SELL, confidence=0.8,
            sentiment_score=-0.8, reasoning="Short signal",
            current_price=150.0,
            stop_loss=156.0,  # Above entry = short
            take_profit=141.0,
        )

        plan = rm.evaluate(
            signal=signal,
            portfolio_value=100000.0,
            cash=80000.0,
            open_position_count=0,
            existing_ticker_positions=[],
            short_exposure=0.0,
        )

        assert isinstance(plan, PositionPlan)
        assert plan.is_short is True
        assert plan.quantity > 0

    def test_short_exposure_limit(self):
        rm = RiskManager(params={
            "max_open_positions": 10,
            "min_cash_reserve_pct": 0.20,
            "max_position_pct": 0.10,
            "max_short_exposure_pct": 0.30,
        })

        signal = Signal(
            ticker="AAPL", side=TradeSide.SELL, confidence=0.8,
            sentiment_score=-0.8, reasoning="Short",
            current_price=150.0, stop_loss=156.0, take_profit=141.0,
        )

        # Already at 30% short exposure
        result = rm.evaluate(
            signal=signal,
            portfolio_value=100000.0,
            cash=80000.0,
            open_position_count=3,
            existing_ticker_positions=["MSFT", "TSLA", "GOOGL"],
            short_exposure=30000.0,
        )

        assert isinstance(result, RiskVeto)
        assert "short exposure" in result.reason.lower()
