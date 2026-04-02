"""Tests for V4 technical indicators: HV, HV Percentile, ATR%, ADX, OBV Trend."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis.technical import TechnicalAnalyzer, TechnicalSnapshot


def _make_ohlcv_df(
    num_bars: int = 300,
    base_price: float = 150.0,
    trend: float = 0.1,
    volatility: float = 2.0,
    base_volume: int = 1000000,
) -> pd.DataFrame:
    np.random.seed(42)
    closes = [base_price]
    for i in range(1, num_bars):
        change = trend + np.random.randn() * volatility
        closes.append(closes[-1] + change)

    closes = np.array(closes)
    highs = closes + np.abs(np.random.randn(num_bars)) * volatility
    lows = closes - np.abs(np.random.randn(num_bars)) * volatility
    opens = closes + np.random.randn(num_bars) * (volatility * 0.5)
    volumes = np.random.randint(
        int(base_volume * 0.5), int(base_volume * 1.5), size=num_bars
    )

    dates = pd.date_range(end="2025-06-01", periods=num_bars, freq="D")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


def _make_trending_df(direction: str = "up", num_bars: int = 300) -> pd.DataFrame:
    """Create a strongly trending DataFrame for ADX testing."""
    np.random.seed(42)
    trend = 1.0 if direction == "up" else -1.0
    closes = [150.0]
    for i in range(1, num_bars):
        change = trend + np.random.randn() * 0.5  # Low noise, strong trend
        closes.append(closes[-1] + change)

    closes = np.array(closes)
    highs = closes + np.abs(np.random.randn(num_bars)) * 0.5
    lows = closes - np.abs(np.random.randn(num_bars)) * 0.5
    opens = closes + np.random.randn(num_bars) * 0.25
    volumes = np.random.randint(800000, 1200000, size=num_bars)

    dates = pd.date_range(end="2025-06-01", periods=num_bars, freq="D")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


def _make_rising_obv_df(num_bars: int = 60) -> pd.DataFrame:
    """Create a DataFrame where price rises on consistently high volume."""
    np.random.seed(42)
    closes = [150.0]
    for i in range(1, num_bars):
        closes.append(closes[-1] + 0.5 + np.random.randn() * 0.2)

    closes = np.array(closes)
    highs = closes + 0.5
    lows = closes - 0.3
    opens = closes - 0.1
    # Volume consistently high on up days
    volumes = np.full(num_bars, 1500000)

    dates = pd.date_range(end="2025-06-01", periods=num_bars, freq="D")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


class TestHistoricalVolatility:
    def test_hv_calculated(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=300)
        snap = analyzer.analyze("AAPL", df)

        assert snap.hv_20 is not None
        assert snap.hv_20 > 0

    def test_hv_reasonable_range(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=300)
        snap = analyzer.analyze("AAPL", df)

        # Annualized HV for typical stocks is 10-80%
        assert 1 < snap.hv_20 < 200

    def test_hv_insufficient_data(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=15)
        snap = analyzer.analyze("AAPL", df)

        assert snap.hv_20 is None

    def test_hv_percentile_calculated(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=300)
        snap = analyzer.analyze("AAPL", df)

        assert snap.hv_percentile is not None
        assert 0 <= snap.hv_percentile <= 100

    def test_hv_percentile_insufficient_data(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snap = analyzer.analyze("AAPL", df)

        # Need 252 + 20 bars for percentile
        assert snap.hv_percentile is None


class TestATRPercent:
    def test_atr_pct_calculated(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snap = analyzer.analyze("AAPL", df)

        assert snap.atr_pct is not None
        assert snap.atr_pct > 0

    def test_atr_pct_reasonable_range(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snap = analyzer.analyze("AAPL", df)

        # ATR% for typical stocks is 0.5-10%
        assert 0.1 < snap.atr_pct < 20


class TestADX:
    def test_adx_calculated(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snap = analyzer.analyze("AAPL", df)

        assert snap.adx_14 is not None
        assert 0 <= snap.adx_14 <= 100

    def test_adx_insufficient_data(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=20)
        snap = analyzer.analyze("AAPL", df)

        assert snap.adx_14 is None

    def test_strong_trend_has_high_adx(self):
        analyzer = TechnicalAnalyzer()
        df = _make_trending_df(direction="up", num_bars=300)
        snap = analyzer.analyze("AAPL", df)

        assert snap.adx_14 is not None
        assert snap.adx_14 > 25  # Strong trend
        assert snap.is_strong_trend is True

    def test_is_strong_trend_property(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            adx_14=30.0,
        )
        assert snap.is_strong_trend is True
        assert snap.is_weak_trend is False

    def test_is_weak_trend_property(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            adx_14=15.0,
        )
        assert snap.is_weak_trend is True
        assert snap.is_strong_trend is False

    def test_adx_none_not_strong_or_weak(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snap.is_strong_trend is False
        assert snap.is_weak_trend is False


class TestOBVTrend:
    def test_obv_trend_calculated(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snap = analyzer.analyze("AAPL", df)

        assert snap.obv_trend is not None
        assert snap.obv_trend in ("rising", "falling", "flat")

    def test_obv_trend_insufficient_data(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=15)
        snap = analyzer.analyze("AAPL", df)

        assert snap.obv_trend is None

    def test_rising_obv(self):
        analyzer = TechnicalAnalyzer()
        df = _make_rising_obv_df(num_bars=60)
        snap = analyzer.analyze("AAPL", df)

        assert snap.obv_trend == "rising"

    def test_obv_confirming_up_property(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            obv_trend="rising",
        )
        assert snap.is_obv_confirming_up is True
        assert snap.is_obv_diverging is False

    def test_obv_diverging_property(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            obv_trend="falling",
        )
        # Price above SMA50 (uptrend) but OBV falling = divergence
        assert snap.is_obv_diverging is True
        assert snap.is_obv_confirming_up is False


class TestVolatilityProperties:
    def test_is_low_volatility(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            hv_percentile=15.0,
        )
        assert snap.is_low_volatility is True
        assert snap.is_high_volatility is False

    def test_is_high_volatility(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            hv_percentile=85.0,
        )
        assert snap.is_high_volatility is True
        assert snap.is_low_volatility is False

    def test_hv_percentile_none(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snap.is_low_volatility is False
        assert snap.is_high_volatility is False


class TestFormatSnapshot:
    """Test that the new indicators appear in the formatted output."""

    def test_full_format(self):
        from src.simulation.thesis_sim import ThesisSimulation
        snap = TechnicalSnapshot(
            ticker="NVDA", rsi_14=35.0, sma_20=240.0, sma_50=250.0,
            atr_14=8.0, current_price=235.0, avg_volume_20=5e7, latest_volume=5e7,
            macd_histogram=-2.0, bb_lower=230.0, bb_middle=245.0, bb_upper=260.0,
            hv_20=42.0, hv_percentile=25.0, atr_pct=3.4, adx_14=38.0, obv_trend="falling",
        )
        # Create a minimal sim instance for the instance method
        sim = ThesisSimulation.__new__(ThesisSimulation)
        sim._spy_snapshot = None
        formatted = sim._format_snapshot(snap)

        assert "HV=42%" in formatted
        assert "25th pctl" in formatted
        assert "ATR%=3.4%" in formatted
        assert "ADX=38" in formatted
        assert "OBV falling" in formatted
