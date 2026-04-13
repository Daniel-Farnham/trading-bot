"""Volatility trigger check for live trading.

Runs every 30 min (no Claude call). Monitors holdings AND watchlist for:
1. Intraday shock — any ticker moves >=1.5x ATR against yesterday's close
   (either direction), or portfolio swings >=5% intraday
2. Volatility drift — portfolio swung >=5% since last Call 3
3. Low volatility — SPY HV below 30th percentile (options cheap)

Zero cooldown. Fires Call 3 immediately if triggered.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

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
        # _prev_prices holds YESTERDAY'S CLOSE for each ticker. Anchoring to
        # a fixed daily reference (not a rolling 30-min check) means a 10%
        # drift over 6 hours still counts as a 10% move — the earlier bug
        # was overwriting this with current prices after every check, so
        # gradual drawdowns never accumulated into a trigger.
        self._prev_prices: dict[str, float] = {}
        self._prev_prices_date: date | None = None
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

        # Make sure _prev_prices has yesterday's close for every ticker we're
        # monitoring. Refreshes on day change, fills in newly-added tickers.
        self._refresh_previous_closes_if_needed(all_tickers)

        # Fetch current prices
        current_prices = self._market.get_latest_prices(all_tickers + ["SPY"])

        # 1. Intraday shock
        shock = self._check_intraday_shock(all_tickers, current_prices, portfolio_value)
        if shock:
            return shock

        # 2. Volatility drift (only if we have a reference point)
        drift = self._check_volatility_drift(portfolio_value)
        if drift:
            return drift

        # 3. Low volatility
        low_vol = self._check_low_vol(current_prices.get("SPY"))
        if low_vol:
            return low_vol

        # NOTE: we intentionally do NOT overwrite _prev_prices here. The
        # reference stays anchored to yesterday's close so gradual drawdowns
        # accumulate into the ATR threshold.
        return None

    def _refresh_previous_closes_if_needed(self, tickers: list[str]) -> None:
        """Ensure _prev_prices has yesterday's close for every ticker.

        Refetches the full set on date change (new trading day) and tops up
        any ticker we don't have yet (e.g., a freshly-bought position that
        wasn't in yesterday's holdings).
        """
        today = date.today()
        if self._prev_prices_date != today:
            self._prev_prices = {}
            self._prev_prices_date = today

        missing = [t for t in tickers if t not in self._prev_prices]
        if missing:
            fetched = self._fetch_previous_closes(missing)
            self._prev_prices.update(fetched)

    def set_last_call3_value(self, value: float) -> None:
        """Set the portfolio value at the time of the last Call 3."""
        self._last_call3_portfolio_value = value

    def _check_intraday_shock(
        self,
        tickers: list[str],
        current_prices: dict[str, float],
        portfolio_value: float,
        atr_multiple: float = 1.5,
        portfolio_threshold: float = 0.05,
    ) -> TriggerResult | None:
        """Check for intraday shocks on holdings + watchlist.

        Compares current price to yesterday's close (anchored, not rolling).
        Fires on moves in either direction once they exceed the ATR-based
        threshold — a +20% rally is just as much a 'review this' signal as
        a -20% drop for a Druckenmiller-style book. If you want to ignore
        rallies, change the `abs()` back to negative-only here.
        """
        if not self._prev_prices:
            # Previous-close fetch happens in _refresh_previous_closes_if_needed.
            # If it failed, we just can't check shocks this cycle — try again.
            return None

        triggered_tickers = []

        for ticker in tickers:
            current = current_prices.get(ticker)
            prev = self._prev_prices.get(ticker)
            if not current or not prev or prev <= 0:
                continue

            day_return = (current - prev) / prev

            # ATR-based threshold from live bars. Fallback 10% if ATR missing.
            threshold_pct = 0.10
            atr_pct = self._get_atr_pct(ticker)
            if atr_pct is not None:
                threshold_pct = (atr_pct / 100.0) * atr_multiple

            if abs(day_return) >= threshold_pct:
                direction = "up" if day_return > 0 else "down"
                triggered_tickers.append(ticker)
                logger.info(
                    "TRIGGER: %s moved %+.1f%% %s ($%.2f → $%.2f) — "
                    "exceeds %.1fx ATR (%.1f%% threshold)",
                    ticker, day_return * 100, direction, prev, current,
                    atr_multiple, threshold_pct * 100,
                )

        if triggered_tickers:
            return TriggerResult(
                trigger_type="intraday_shock",
                details=f"Shock on {', '.join(triggered_tickers)}",
                triggered_tickers=triggered_tickers,
            )

        # Portfolio-level check — fire on absolute swing in either direction
        if self._last_call3_portfolio_value:
            prev_value = self._last_call3_portfolio_value
            if prev_value > 0:
                portfolio_return = (portfolio_value - prev_value) / prev_value
                if abs(portfolio_return) >= portfolio_threshold:
                    direction = "up" if portfolio_return > 0 else "down"
                    logger.info(
                        "TRIGGER: Portfolio swung %+.1f%% %s ($%s → $%s)",
                        portfolio_return * 100, direction,
                        f"{prev_value:,.0f}", f"{portfolio_value:,.0f}",
                    )
                    return TriggerResult(
                        trigger_type="intraday_shock",
                        details=f"Portfolio swung {portfolio_return*100:+.1f}%",
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

    def _fetch_previous_closes(self, tickers: list[str]) -> dict[str, float]:
        """Fetch previous day's closing prices from Alpaca bars.

        Used as the reference point on the first check of the day so that
        overnight gaps are always caught, even after bot restarts.
        """
        closes = {}
        today = date.today()
        for ticker in tickers:
            try:
                bars = self._market.get_bars(
                    ticker, start=datetime.now() - timedelta(days=7), limit=5,
                )
                if bars.empty:
                    continue
                # Walk backwards past any bar dated today — during market
                # hours Alpaca includes today's in-progress bar which is
                # NOT a valid "previous close" reference.
                for i in range(len(bars) - 1, -1, -1):
                    row = bars.iloc[i]
                    bar_date = row.name.date() if hasattr(row.name, "date") else None
                    if bar_date and bar_date >= today:
                        continue
                    closes[ticker] = float(row["close"])
                    break
            except Exception as e:
                logger.warning("Failed to fetch previous close for %s: %s", ticker, e)
                continue
        if closes:
            logger.info("Loaded previous closes for %d tickers as trigger reference", len(closes))
        return closes

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
