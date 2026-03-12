from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator
from ta.volatility import AverageTrueRange


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
    def has_volume_spike(self) -> bool:
        if self.avg_volume_20 is None or self.latest_volume is None:
            return False
        if self.avg_volume_20 == 0:
            return False
        return self.latest_volume > (self.avg_volume_20 * 1.5)


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

        return TechnicalSnapshot(
            ticker=ticker,
            rsi_14=rsi_14,
            sma_20=sma_20,
            sma_50=sma_50,
            atr_14=atr_14,
            current_price=current_price,
            avg_volume_20=avg_volume_20,
            latest_volume=latest_volume,
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
