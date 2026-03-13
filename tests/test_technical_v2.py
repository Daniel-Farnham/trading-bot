"""Tests for MACD and Bollinger Bands additions to technical analysis."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis.technical import TechnicalAnalyzer, TechnicalSnapshot


def _make_ohlcv_df(
    num_bars: int = 60,
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


class TestMACD:
    def test_macd_calculated(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snap = analyzer.analyze("AAPL", df)

        assert snap.macd_line is not None
        assert snap.macd_signal is not None
        assert snap.macd_histogram is not None

    def test_macd_insufficient_data(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=20)
        snap = analyzer.analyze("AAPL", df)

        assert snap.macd_line is None
        assert snap.macd_signal is None
        assert snap.macd_histogram is None

    def test_is_macd_bullish(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            macd_line=1.5, macd_signal=1.0, macd_histogram=0.5,
        )
        assert snap.is_macd_bullish is True
        assert snap.is_macd_bearish is False

    def test_is_macd_bearish(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            macd_line=0.5, macd_signal=1.0, macd_histogram=-0.5,
        )
        assert snap.is_macd_bullish is False
        assert snap.is_macd_bearish is True

    def test_macd_none_not_bullish_or_bearish(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snap.is_macd_bullish is False
        assert snap.is_macd_bearish is False


class TestBollingerBands:
    def test_bb_calculated(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snap = analyzer.analyze("AAPL", df)

        assert snap.bb_upper is not None
        assert snap.bb_middle is not None
        assert snap.bb_lower is not None
        assert snap.bb_width is not None
        assert snap.bb_upper > snap.bb_middle > snap.bb_lower

    def test_bb_insufficient_data(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=10)
        snap = analyzer.analyze("AAPL", df)

        assert snap.bb_upper is None
        assert snap.bb_lower is None

    def test_is_near_lower_band(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=25.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=91.0, avg_volume_20=1e6, latest_volume=1e6,
            bb_upper=110.0, bb_middle=100.0, bb_lower=90.0,
        )
        assert snap.is_near_lower_band is True
        assert snap.is_near_upper_band is False

    def test_is_near_upper_band(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=75.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=109.0, avg_volume_20=1e6, latest_volume=1e6,
            bb_upper=110.0, bb_middle=100.0, bb_lower=90.0,
        )
        assert snap.is_near_upper_band is True
        assert snap.is_near_lower_band is False

    def test_not_near_either_band(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            bb_upper=110.0, bb_middle=100.0, bb_lower=90.0,
        )
        assert snap.is_near_lower_band is False
        assert snap.is_near_upper_band is False

    def test_bb_squeeze(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            bb_upper=101.0, bb_middle=100.0, bb_lower=99.0, bb_width=2.0,
        )
        assert snap.is_bb_squeeze is True

    def test_no_bb_squeeze(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
            bb_upper=110.0, bb_middle=100.0, bb_lower=90.0, bb_width=20.0,
        )
        assert snap.is_bb_squeeze is False


class TestDowntrend:
    def test_is_downtrend(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=95.0, sma_50=100.0,
            atr_14=2.0, current_price=93.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snap.is_downtrend is True

    def test_not_downtrend(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=105.0, sma_50=100.0,
            atr_14=2.0, current_price=107.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snap.is_downtrend is False

    def test_downtrend_none_sma(self):
        snap = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=None, sma_50=None,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snap.is_downtrend is False
