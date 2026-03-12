from __future__ import annotations

from dataclasses import dataclass

from src.analysis.sentiment import SentimentAnalyzer
from src.analysis.technical import TechnicalSnapshot
from src.config import CONFIG
from src.storage.models import Signal, SentimentRecord, TradeSide
from src.strategy.themes import ThemeManager


@dataclass
class SignalContext:
    """All the data needed to evaluate a signal for one ticker."""
    ticker: str
    sentiment_records: list[SentimentRecord]
    technicals: TechnicalSnapshot
    avg_sentiment: float


class SignalGenerator:
    def __init__(
        self,
        theme_manager: ThemeManager | None = None,
        params: dict | None = None,
    ):
        self._themes = theme_manager or ThemeManager()
        self._params = params or CONFIG.get("trading", {})

    def evaluate(self, ctx: SignalContext) -> Signal | None:
        """Evaluate whether a ticker warrants a trade signal.

        Returns a Signal if conditions are met, None otherwise.
        """
        buy_signal = self._check_buy(ctx)
        if buy_signal:
            return buy_signal

        sell_signal = self._check_sell(ctx)
        if sell_signal:
            return sell_signal

        return None

    def _check_buy(self, ctx: SignalContext) -> Signal | None:
        buy_threshold = self._params.get("sentiment_buy_threshold", 0.6)
        rsi_overbought = self._params.get("rsi_overbought", 70)
        atr_sl_mult = self._params.get("atr_stop_loss_multiplier", 2.0)
        atr_tp_mult = self._params.get("atr_take_profit_multiplier", 3.0)

        # Core condition: sentiment must be above threshold
        if ctx.avg_sentiment < buy_threshold:
            return None

        # Filter: don't buy overbought stocks
        if ctx.technicals.rsi_14 is not None and ctx.technicals.rsi_14 > rsi_overbought:
            return None

        # Build confidence from multiple factors
        confidence = self._calc_buy_confidence(ctx)

        # Apply theme nudge
        confidence = self._themes.apply_theme_nudge(ctx.ticker, confidence)

        # Minimum confidence threshold
        if confidence < 0.3:
            return None

        # Calculate stop-loss and take-profit using ATR
        price = ctx.technicals.current_price
        atr = ctx.technicals.atr_14

        if atr is None or atr == 0 or price == 0:
            return None

        stop_loss = price - (atr * atr_sl_mult)
        take_profit = price + (atr * atr_tp_mult)

        reasoning = self._build_buy_reasoning(ctx, confidence)

        return Signal(
            ticker=ctx.ticker,
            side=TradeSide.BUY,
            confidence=round(confidence, 3),
            sentiment_score=round(ctx.avg_sentiment, 3),
            reasoning=reasoning,
            current_price=price,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
        )

    def _check_sell(self, ctx: SignalContext) -> Signal | None:
        sell_threshold = self._params.get("sentiment_sell_threshold", -0.4)

        if ctx.avg_sentiment > sell_threshold:
            return None

        price = ctx.technicals.current_price
        if price == 0:
            return None

        confidence = min(1.0, abs(ctx.avg_sentiment))

        reasoning = (
            f"Negative sentiment ({ctx.avg_sentiment:.2f}) below threshold "
            f"({sell_threshold}). Recommending exit."
        )

        return Signal(
            ticker=ctx.ticker,
            side=TradeSide.SELL,
            confidence=round(confidence, 3),
            sentiment_score=round(ctx.avg_sentiment, 3),
            reasoning=reasoning,
            current_price=price,
            stop_loss=0.0,
            take_profit=0.0,
        )

    def _calc_buy_confidence(self, ctx: SignalContext) -> float:
        """Calculate confidence from 0-1 based on how many factors align."""
        score = 0.0
        factors = 0

        # Sentiment strength (0 to 0.4)
        buy_threshold = self._params.get("sentiment_buy_threshold", 0.6)
        sentiment_excess = ctx.avg_sentiment - buy_threshold
        score += min(0.4, sentiment_excess * 2)
        factors += 1

        # Trend alignment (0 or 0.2)
        if ctx.technicals.is_uptrend:
            score += 0.2
        factors += 1

        # RSI in healthy range (0 or 0.2)
        if ctx.technicals.rsi_14 is not None:
            if 30 < ctx.technicals.rsi_14 < 60:
                score += 0.2  # Ideal buying zone
            elif ctx.technicals.rsi_14 <= 30:
                score += 0.15  # Oversold — could bounce
        factors += 1

        # Volume confirmation (0 or 0.2)
        if ctx.technicals.has_volume_spike:
            score += 0.2
        factors += 1

        return min(1.0, score)

    def _build_buy_reasoning(self, ctx: SignalContext, confidence: float) -> str:
        parts = [f"Sentiment: {ctx.avg_sentiment:.2f}"]

        if ctx.technicals.is_uptrend:
            parts.append("uptrend confirmed")
        if ctx.technicals.has_volume_spike:
            parts.append("volume spike detected")
        if ctx.technicals.rsi_14 is not None:
            parts.append(f"RSI: {ctx.technicals.rsi_14:.1f}")

        theme_score = self._themes.get_composite_score(ctx.ticker)
        if abs(theme_score) > 0.1:
            direction = "aligned" if theme_score > 0 else "misaligned"
            parts.append(f"theme {direction} ({theme_score:+.2f})")

        parts.append(f"confidence: {confidence:.2f}")
        return ". ".join(parts)
