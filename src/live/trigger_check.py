"""Volatility trigger check for live trading.

Runs every 30 min (no Claude call). Monitors holdings AND watchlist for:
1. Intraday shock — any ticker moves >3x ATR, or portfolio drops >5%
2. Volatility drift — portfolio swung >5% since last Call 3
3. Low volatility — SPY HV below 30th percentile (options cheap)

Zero cooldown. Fires Call 3 immediately if triggered.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.analysis.technical import TechnicalAnalyzer
from src.data.market import MarketData

logger = logging.getLogger(__name__)


@dataclass
class TriggerResult:
    trigger_type: str  # "intraday_shock" | "volatility_drift" | "low_volatility"
    details: str
    triggered_tickers: list[str]


class TriggerCheck:
    """Monitors holdings + watchlist for volatility events. No Claude call."""

    def __init__(
        self,
        market_data: MarketData,
        technical_analyzer: TechnicalAnalyzer,
    ):
        self._market = market_data
        self._technicals = technical_analyzer
        self._last_call3_portfolio_value: float | None = None
        self._prev_prices: dict[str, float] = {}
        self._spy_hv_prev: float = 50.0  # Start neutral for debounce

    def check(
        self,
        holdings_tickers: list[str],
        watchlist_tickers: list[str],
        portfolio_value: float,
    ) -> TriggerResult | None:
        """Run all trigger checks. Returns TriggerResult or None.

        Args:
            holdings_tickers: Currently held positions.
            watchlist_tickers: Watchlisted tickers (also monitored for shocks).
            portfolio_value: Current portfolio value from Alpaca.
        """
        all_tickers = list(set(holdings_tickers + watchlist_tickers))

        # Fetch current prices
        current_prices = self._market.get_latest_prices(all_tickers + ["SPY"])

        # 1. Intraday shock
        shock = self._check_intraday_shock(all_tickers, current_prices, portfolio_value)
        if shock:
            self._prev_prices = current_prices
            return shock

        # 2. Volatility drift (only if we have a reference point)
        drift = self._check_volatility_drift(portfolio_value)
        if drift:
            self._prev_prices = current_prices
            return drift

        # 3. Low volatility
        low_vol = self._check_low_vol(current_prices.get("SPY"))
        if low_vol:
            self._prev_prices = current_prices
            return low_vol

        # Update previous prices for next check
        self._prev_prices = current_prices
        return None

    def set_last_call3_value(self, value: float) -> None:
        """Set the portfolio value at the time of the last Call 3."""
        self._last_call3_portfolio_value = value

    def _check_intraday_shock(
        self,
        tickers: list[str],
        current_prices: dict[str, float],
        portfolio_value: float,
        atr_multiple: float = 3.0,
        portfolio_threshold: float = -0.05,
    ) -> TriggerResult | None:
        """Check for intraday shocks on holdings + watchlist."""
        if not self._prev_prices:
            return None  # First check of the day, no reference

        triggered_tickers = []

        for ticker in tickers:
            current = current_prices.get(ticker)
            prev = self._prev_prices.get(ticker)
            if not current or not prev or prev <= 0:
                continue

            day_return = (current - prev) / prev

            # Compute ATR threshold from live bars
            threshold = -0.10  # Fallback
            atr_pct = self._get_atr_pct(ticker)
            if atr_pct is not None:
                threshold = -(atr_pct / 100.0) * atr_multiple

            if day_return <= threshold:
                triggered_tickers.append(ticker)
                logger.info(
                    "TRIGGER: %s moved %.1f%% ($%.2f → $%.2f) — exceeds %.1fx ATR",
                    ticker, day_return * 100, prev, current, atr_multiple,
                )

        if triggered_tickers:
            return TriggerResult(
                trigger_type="intraday_shock",
                details=f"Shock on {', '.join(triggered_tickers)}",
                triggered_tickers=triggered_tickers,
            )

        # Portfolio-level check
        if self._prev_prices and self._last_call3_portfolio_value:
            # Use start-of-day value as reference for intraday
            prev_value = self._last_call3_portfolio_value
            if prev_value > 0:
                portfolio_return = (portfolio_value - prev_value) / prev_value
                if portfolio_return <= portfolio_threshold:
                    logger.info(
                        "TRIGGER: Portfolio dropped %.1f%% ($%s → $%s)",
                        portfolio_return * 100,
                        f"{prev_value:,.0f}", f"{portfolio_value:,.0f}",
                    )
                    return TriggerResult(
                        trigger_type="intraday_shock",
                        details=f"Portfolio dropped {portfolio_return*100:.1f}%",
                        triggered_tickers=[],
                    )

        return None

    def _check_volatility_drift(
        self,
        current_value: float,
        threshold: float = 0.05,
    ) -> TriggerResult | None:
        """Check if portfolio has swung 5%+ since last Call 3."""
        if self._last_call3_portfolio_value is None:
            return None

        ref = self._last_call3_portfolio_value
        if ref <= 0:
            return None

        swing = abs(current_value - ref) / ref
        if swing >= threshold:
            direction = "up" if current_value > ref else "down"
            logger.info(
                "TRIGGER: Portfolio swung %.1f%% %s since last Call 3 ($%s → $%s)",
                swing * 100, direction, f"{ref:,.0f}", f"{current_value:,.0f}",
            )
            return TriggerResult(
                trigger_type="volatility_drift",
                details=f"Portfolio swung {swing*100:.1f}% {direction} since last Call 3",
                triggered_tickers=[],
            )
        return None

    def _check_low_vol(
        self,
        spy_price: float | None,
        hv_threshold: float = 30.0,
    ) -> TriggerResult | None:
        """Check if SPY HV has dropped below threshold (options cheap)."""
        if spy_price is None:
            return None

        spy_hv = self._get_spy_hv_percentile()
        if spy_hv is None:
            return None

        if spy_hv < hv_threshold:
            # Debounce: only fire once per calm period
            if self._spy_hv_prev < hv_threshold:
                return None
            logger.info(
                "TRIGGER: SPY HV at %.0fth percentile — low volatility, options premiums cheap",
                spy_hv,
            )
            self._spy_hv_prev = spy_hv
            return TriggerResult(
                trigger_type="low_volatility",
                details=f"SPY HV at {spy_hv:.0f}th percentile — options cheap",
                triggered_tickers=[],
            )

        self._spy_hv_prev = spy_hv
        return None

    def _get_atr_pct(self, ticker: str) -> float | None:
        """Compute ATR% from live Alpaca bars."""
        try:
            start = datetime.now() - timedelta(days=30)
            bars = self._market.get_bars(ticker, start=start, limit=30)
            if bars.empty or len(bars) < 14:
                return None
            snap = self._technicals.analyze(ticker, bars)
            return snap.atr_pct
        except Exception:
            return None

    def _get_spy_hv_percentile(self) -> float | None:
        """Compute SPY HV percentile from live Alpaca bars."""
        try:
            start = datetime.now() - timedelta(days=90)
            bars = self._market.get_bars("SPY", start=start, limit=90)
            if bars.empty or len(bars) < 20:
                return None
            snap = self._technicals.analyze("SPY", bars)
            return snap.hv_percentile
        except Exception:
            return None
