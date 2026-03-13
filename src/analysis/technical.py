from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD
from ta.volatility import AverageTrueRange, BollingerBands


@dataclass
class TechnicalSnapshot:
    """Point-in-time technical indicator values for a ticker."""
    ticker: str
    rsi_14: float | None
    sma_20: float | None
    sma_50: float | None
    atr_14: float | None
    current_price: float
    avg_volume_20: float | None
    latest_volume: float | None
    # MACD
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    # Bollinger Bands
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    bb_width: float | None = None

    @property
    def is_overbought(self) -> bool:
        return self.rsi_14 is not None and self.rsi_14 > 70

    @property
    def is_oversold(self) -> bool:
        return self.rsi_14 is not None and self.rsi_14 < 30

    @property
    def is_uptrend(self) -> bool:
        if self.sma_50 is None:
            return False
        return self.current_price > self.sma_50

    @property
    def is_downtrend(self) -> bool:
        if self.sma_20 is None or self.sma_50 is None:
            return False
        return self.sma_20 < self.sma_50

    @property
    def has_volume_spike(self) -> bool:
        if self.avg_volume_20 is None or self.latest_volume is None:
            return False
        if self.avg_volume_20 == 0:
            return False
        return self.latest_volume > (self.avg_volume_20 * 1.5)

    @property
    def is_macd_bullish(self) -> bool:
        if self.macd_histogram is None:
            return False
        return self.macd_histogram > 0

    @property
    def is_macd_bearish(self) -> bool:
        if self.macd_histogram is None:
            return False
        return self.macd_histogram < 0

    @property
    def is_near_lower_band(self) -> bool:
        if self.bb_lower is None or self.bb_middle is None or self.current_price == 0:
            return False
        band_range = self.bb_middle - self.bb_lower
        if band_range <= 0:
            return False
        # Price is within 20% of the lower band
        return (self.current_price - self.bb_lower) < (band_range * 0.2)

    @property
    def is_near_upper_band(self) -> bool:
        if self.bb_upper is None or self.bb_middle is None or self.current_price == 0:
            return False
        band_range = self.bb_upper - self.bb_middle
        if band_range <= 0:
            return False
        # Price is within 20% of the upper band
        return (self.bb_upper - self.current_price) < (band_range * 0.2)

    @property
    def is_bb_squeeze(self) -> bool:
        """Bollinger Band squeeze — bands narrowing, breakout imminent."""
        if self.bb_width is None or self.current_price == 0:
            return False
        # Width relative to price < 3% is considered a squeeze
        return (self.bb_width / self.current_price) < 0.03


class TechnicalAnalyzer:
    """Calculates technical indicators from OHLCV price data."""

    def analyze(self, ticker: str, df: pd.DataFrame) -> TechnicalSnapshot:
        """Analyze a DataFrame of OHLCV bars.

        Expected columns: open, high, low, close, volume
        Must have at least 50 rows for all indicators to work.
        """
        if df.empty:
            return TechnicalSnapshot(
                ticker=ticker,
                rsi_14=None,
                sma_20=None,
                sma_50=None,
                atr_14=None,
                current_price=0.0,
                avg_volume_20=None,
                latest_volume=None,
            )

        close = df["close"]
        current_price = float(close.iloc[-1])

        rsi_14 = self._calc_rsi(close)
        sma_20 = self._calc_sma(close, 20)
        sma_50 = self._calc_sma(close, 50)
        atr_14 = self._calc_atr(df)
        avg_volume_20 = self._calc_avg_volume(df, 20)
        latest_volume = float(df["volume"].iloc[-1]) if "volume" in df.columns else None

        # MACD
        macd_line, macd_signal, macd_histogram = self._calc_macd(close)

        # Bollinger Bands
        bb_upper, bb_middle, bb_lower, bb_width = self._calc_bollinger(close)

        return TechnicalSnapshot(
            ticker=ticker,
            rsi_14=rsi_14,
            sma_20=sma_20,
            sma_50=sma_50,
            atr_14=atr_14,
            current_price=current_price,
            avg_volume_20=avg_volume_20,
            latest_volume=latest_volume,
            macd_line=macd_line,
            macd_signal=macd_signal,
            macd_histogram=macd_histogram,
            bb_upper=bb_upper,
            bb_middle=bb_middle,
            bb_lower=bb_lower,
            bb_width=bb_width,
        )

    def _calc_rsi(self, close: pd.Series, window: int = 14) -> float | None:
        if len(close) < window + 1:
            return None
        rsi = RSIIndicator(close=close, window=window).rsi()
        val = rsi.iloc[-1]
        return float(val) if pd.notna(val) else None

    def _calc_sma(self, close: pd.Series, window: int) -> float | None:
        if len(close) < window:
            return None
        sma = SMAIndicator(close=close, window=window).sma_indicator()
        val = sma.iloc[-1]
        return float(val) if pd.notna(val) else None

    def _calc_atr(self, df: pd.DataFrame, window: int = 14) -> float | None:
        required = {"high", "low", "close"}
        if not required.issubset(df.columns) or len(df) < window + 1:
            return None
        atr = AverageTrueRange(
            high=df["high"], low=df["low"], close=df["close"], window=window
        ).average_true_range()
        val = atr.iloc[-1]
        return float(val) if pd.notna(val) else None

    def _calc_avg_volume(self, df: pd.DataFrame, window: int = 20) -> float | None:
        if "volume" not in df.columns or len(df) < window:
            return None
        return float(df["volume"].tail(window).mean())

    def _calc_macd(
        self, close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> tuple[float | None, float | None, float | None]:
        if len(close) < slow + signal:
            return None, None, None
        macd = MACD(close=close, window_fast=fast, window_slow=slow, window_sign=signal)
        macd_val = macd.macd().iloc[-1]
        signal_val = macd.macd_signal().iloc[-1]
        hist_val = macd.macd_diff().iloc[-1]
        return (
            float(macd_val) if pd.notna(macd_val) else None,
            float(signal_val) if pd.notna(signal_val) else None,
            float(hist_val) if pd.notna(hist_val) else None,
        )

    def _calc_bollinger(
        self, close: pd.Series, window: int = 20, std: int = 2
    ) -> tuple[float | None, float | None, float | None, float | None]:
        if len(close) < window:
            return None, None, None, None
        bb = BollingerBands(close=close, window=window, window_dev=std)
        upper = bb.bollinger_hband().iloc[-1]
        middle = bb.bollinger_mavg().iloc[-1]
        lower = bb.bollinger_lband().iloc[-1]
        width = bb.bollinger_wband().iloc[-1]
        return (
            float(upper) if pd.notna(upper) else None,
            float(middle) if pd.notna(middle) else None,
            float(lower) if pd.notna(lower) else None,
            float(width) if pd.notna(width) else None,
        )
