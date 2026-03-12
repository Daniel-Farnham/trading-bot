from __future__ import annotations

import pytest

from src.analysis.technical import TechnicalSnapshot
from src.storage.models import SentimentRecord, TradeSide
from src.strategy.signals import SignalContext, SignalGenerator
from src.strategy.themes import ThemeAlignment, ThemeManager


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


def _make_sentiment_records(ticker: str, score: float, count: int = 3) -> list[SentimentRecord]:
    return [
        SentimentRecord(ticker, f"Headline {i}", "test", score, "2025-06-01")
        for i in range(count)
    ]


def _make_context(
    ticker: str = "AAPL",
    avg_sentiment: float = 0.8,
    technicals: TechnicalSnapshot | None = None,
) -> SignalContext:
    if technicals is None:
        technicals = _make_technicals(ticker=ticker)
    records = _make_sentiment_records(ticker, avg_sentiment)
    return SignalContext(
        ticker=ticker,
        sentiment_records=records,
        technicals=technicals,
        avg_sentiment=avg_sentiment,
    )


class TestSignalGeneratorBuy:
    def test_generates_buy_on_positive_sentiment(self):
        gen = SignalGenerator()
        ctx = _make_context(avg_sentiment=0.8)
        signal = gen.evaluate(ctx)

        assert signal is not None
        assert signal.side == TradeSide.BUY
        assert signal.ticker == "AAPL"
        assert signal.confidence > 0

    def test_no_signal_below_threshold(self):
        gen = SignalGenerator()
        ctx = _make_context(avg_sentiment=0.3)
        signal = gen.evaluate(ctx)

        assert signal is None

    def test_no_buy_when_overbought(self):
        gen = SignalGenerator()
        technicals = _make_technicals(rsi_14=80.0)
        ctx = _make_context(avg_sentiment=0.8, technicals=technicals)
        signal = gen.evaluate(ctx)

        assert signal is None

    def test_stop_loss_and_take_profit_set(self):
        gen = SignalGenerator()
        ctx = _make_context(avg_sentiment=0.8)
        signal = gen.evaluate(ctx)

        assert signal is not None
        assert signal.stop_loss < signal.current_price
        assert signal.take_profit > signal.current_price

    def test_stop_loss_uses_atr(self):
        gen = SignalGenerator()
        technicals = _make_technicals(atr_14=5.0, current_price=100.0)
        ctx = _make_context(avg_sentiment=0.8, technicals=technicals)
        signal = gen.evaluate(ctx)

        assert signal is not None
        # Default: 2x ATR stop loss = 100 - 10 = 90
        assert signal.stop_loss == 90.0
        # Default: 3x ATR take profit = 100 + 15 = 115
        assert signal.take_profit == 115.0

    def test_no_signal_when_no_atr(self):
        gen = SignalGenerator()
        technicals = _make_technicals(atr_14=None)
        ctx = _make_context(avg_sentiment=0.8, technicals=technicals)
        signal = gen.evaluate(ctx)

        assert signal is None

    def test_confidence_higher_with_uptrend(self):
        gen = SignalGenerator()

        # Uptrend: price > sma_50
        ctx_up = _make_context(
            avg_sentiment=0.8,
            technicals=_make_technicals(current_price=155.0, sma_50=145.0),
        )
        # Downtrend: price < sma_50
        ctx_down = _make_context(
            avg_sentiment=0.8,
            technicals=_make_technicals(current_price=140.0, sma_50=145.0),
        )

        signal_up = gen.evaluate(ctx_up)
        signal_down = gen.evaluate(ctx_down)

        assert signal_up is not None
        assert signal_down is not None
        assert signal_up.confidence > signal_down.confidence

    def test_confidence_higher_with_volume_spike(self):
        gen = SignalGenerator()

        ctx_spike = _make_context(
            avg_sentiment=0.8,
            technicals=_make_technicals(latest_volume=2e6, avg_volume_20=1e6),
        )
        ctx_normal = _make_context(
            avg_sentiment=0.8,
            technicals=_make_technicals(latest_volume=1e6, avg_volume_20=1e6),
        )

        signal_spike = gen.evaluate(ctx_spike)
        signal_normal = gen.evaluate(ctx_normal)

        assert signal_spike is not None
        assert signal_normal is not None
        assert signal_spike.confidence > signal_normal.confidence


class TestSignalGeneratorSell:
    def test_generates_sell_on_negative_sentiment(self):
        gen = SignalGenerator()
        ctx = _make_context(avg_sentiment=-0.6)
        signal = gen.evaluate(ctx)

        assert signal is not None
        assert signal.side == TradeSide.SELL

    def test_no_sell_above_threshold(self):
        gen = SignalGenerator()
        ctx = _make_context(avg_sentiment=-0.2)
        signal = gen.evaluate(ctx)

        # Not negative enough to sell, not positive enough to buy
        assert signal is None


class TestSignalGeneratorWithThemes:
    def test_theme_boosts_confidence(self):
        manager = ThemeManager(themes=[])
        manager._alignments = {
            "ENPH": [ThemeAlignment("ENPH", "climate", 0.9, "Solar")],
        }

        gen = SignalGenerator(theme_manager=manager)
        ctx = _make_context(ticker="ENPH", avg_sentiment=0.8)
        ctx.technicals = _make_technicals(ticker="ENPH")
        signal = gen.evaluate(ctx)

        # Compare to signal without themes
        gen_no_theme = SignalGenerator(theme_manager=ThemeManager(themes=[]))
        signal_no_theme = gen_no_theme.evaluate(ctx)

        assert signal is not None
        assert signal_no_theme is not None
        assert signal.confidence > signal_no_theme.confidence

    def test_theme_reduces_confidence(self):
        manager = ThemeManager(themes=[])
        manager._alignments = {
            "XOM": [ThemeAlignment("XOM", "climate", -0.8, "Oil")],
        }

        gen = SignalGenerator(theme_manager=manager)
        ctx = _make_context(ticker="XOM", avg_sentiment=0.8)
        ctx.technicals = _make_technicals(ticker="XOM")
        signal = gen.evaluate(ctx)

        gen_no_theme = SignalGenerator(theme_manager=ThemeManager(themes=[]))
        signal_no_theme = gen_no_theme.evaluate(ctx)

        assert signal is not None
        assert signal_no_theme is not None
        assert signal.confidence < signal_no_theme.confidence

    def test_reasoning_includes_theme(self):
        manager = ThemeManager(themes=[])
        manager._alignments = {
            "ENPH": [ThemeAlignment("ENPH", "climate", 0.9, "Solar")],
        }

        gen = SignalGenerator(theme_manager=manager)
        ctx = _make_context(ticker="ENPH", avg_sentiment=0.8)
        ctx.technicals = _make_technicals(ticker="ENPH")
        signal = gen.evaluate(ctx)

        assert signal is not None
        assert "theme" in signal.reasoning.lower()
