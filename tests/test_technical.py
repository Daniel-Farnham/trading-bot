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
    """Generate a synthetic OHLCV DataFrame for testing."""
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
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=dates,
    )


def _make_volume_spike_df() -> pd.DataFrame:
    """DataFrame where the last bar has a clear volume spike."""
    df = _make_ohlcv_df(num_bars=30, base_volume=1000000)
    # Set the last bar's volume to 3x the average
    df.iloc[-1, df.columns.get_loc("volume")] = 3000000
    return df


class TestTechnicalAnalyzer:
    def test_analyze_returns_snapshot(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snapshot = analyzer.analyze("AAPL", df)

        assert isinstance(snapshot, TechnicalSnapshot)
        assert snapshot.ticker == "AAPL"
        assert snapshot.current_price > 0

    def test_rsi_in_valid_range(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snapshot = analyzer.analyze("AAPL", df)

        assert snapshot.rsi_14 is not None
        assert 0 <= snapshot.rsi_14 <= 100

    def test_sma_values_calculated(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snapshot = analyzer.analyze("AAPL", df)

        assert snapshot.sma_20 is not None
        assert snapshot.sma_50 is not None
        assert snapshot.sma_20 > 0
        assert snapshot.sma_50 > 0

    def test_atr_positive(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snapshot = analyzer.analyze("AAPL", df)

        assert snapshot.atr_14 is not None
        assert snapshot.atr_14 > 0

    def test_volume_values(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=60)
        snapshot = analyzer.analyze("AAPL", df)

        assert snapshot.avg_volume_20 is not None
        assert snapshot.latest_volume is not None
        assert snapshot.avg_volume_20 > 0
        assert snapshot.latest_volume > 0

    def test_insufficient_data_for_sma50(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=30)
        snapshot = analyzer.analyze("AAPL", df)

        assert snapshot.sma_20 is not None
        assert snapshot.sma_50 is None  # Not enough data

    def test_insufficient_data_for_rsi(self):
        analyzer = TechnicalAnalyzer()
        df = _make_ohlcv_df(num_bars=10)
        snapshot = analyzer.analyze("AAPL", df)

        assert snapshot.rsi_14 is None

    def test_empty_dataframe(self):
        analyzer = TechnicalAnalyzer()
        df = pd.DataFrame()
        snapshot = analyzer.analyze("AAPL", df)

        assert snapshot.current_price == 0.0
        assert snapshot.rsi_14 is None
        assert snapshot.sma_20 is None
        assert snapshot.sma_50 is None
        assert snapshot.atr_14 is None


class TestTechnicalSnapshot:
    def test_is_overbought(self):
        snapshot = TechnicalSnapshot(
            ticker="X", rsi_14=75.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=105.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snapshot.is_overbought is True
        assert snapshot.is_oversold is False

    def test_is_oversold(self):
        snapshot = TechnicalSnapshot(
            ticker="X", rsi_14=25.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=90.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snapshot.is_oversold is True
        assert snapshot.is_overbought is False

    def test_neither_overbought_nor_oversold(self):
        snapshot = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snapshot.is_overbought is False
        assert snapshot.is_oversold is False

    def test_is_uptrend(self):
        snapshot = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snapshot.is_uptrend is True  # price 100 > sma_50 95

    def test_is_downtrend(self):
        snapshot = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=105.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snapshot.is_uptrend is False  # price 100 < sma_50 105

    def test_uptrend_none_sma50(self):
        snapshot = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=None,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1e6,
        )
        assert snapshot.is_uptrend is False  # Can't determine trend

    def test_has_volume_spike(self):
        snapshot = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=2e6,
        )
        assert snapshot.has_volume_spike is True  # 2M > 1.5M (1M * 1.5)

    def test_no_volume_spike(self):
        snapshot = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=1e6, latest_volume=1.2e6,
        )
        assert snapshot.has_volume_spike is False  # 1.2M < 1.5M

    def test_volume_spike_none_values(self):
        snapshot = TechnicalSnapshot(
            ticker="X", rsi_14=50.0, sma_20=100.0, sma_50=95.0,
            atr_14=2.0, current_price=100.0, avg_volume_20=None, latest_volume=None,
        )
        assert snapshot.has_volume_spike is False

    def test_rsi_none_not_overbought(self):
        snapshot = TechnicalSnapshot(
            ticker="X", rsi_14=None, sma_20=None, sma_50=None,
            atr_14=None, current_price=100.0, avg_volume_20=None, latest_volume=None,
        )
        assert snapshot.is_overbought is False
        assert snapshot.is_oversold is False
