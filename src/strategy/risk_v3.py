"""V3 Risk Manager — allocation-based sizing with wide catastrophic stops.

Unlike V2's signal-based risk evaluation, V3 takes Claude's allocation percentages
and converts them to share counts while enforcing portfolio-level constraints.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from src.config import CONFIG

logger = logging.getLogger(__name__)


@dataclass
class PositionPlan:
    """A risk-validated plan for executing a trade."""
    ticker: str
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float  # Dollar amount at risk
    position_value: float  # Total position value
    risk_pct: float  # Percentage of portfolio at risk
    is_short: bool = False


def _portfolio_cfg(key: str, default):
    return CONFIG.get("portfolio", CONFIG.get("trading", {})).get(key, default)


@dataclass
class V3PositionPlan:
    """A risk-validated plan for a V3 thesis-driven trade."""
    ticker: str
    side: str  # "LONG" or "SHORT"
    quantity: int
    entry_price: float
    catastrophic_stop: float
    allocation_pct: float
    position_value: float
    thesis_ref: str


@dataclass
class V3RiskVeto:
    """Reason a trade was rejected."""
    ticker: str
    reason: str


class RiskManagerV3:
    """Enforces V3 portfolio constraints and sizes positions from allocation %."""

    def __init__(self, params: dict | None = None):
        p = params or {}
        self._max_positions = p.get("max_positions", _portfolio_cfg("max_positions", 8))
        self._max_single_pct = p.get("max_single_position_pct", _portfolio_cfg("max_single_position_pct", 0.20))
        self._min_cash_pct = p.get("min_cash_reserve_pct", _portfolio_cfg("min_cash_reserve_pct", 0.20))
        self._catastrophic_stop_pct = p.get("catastrophic_stop_pct", _portfolio_cfg("catastrophic_stop_pct", 0.30))
        self._max_short_pct = p.get("max_short_exposure_pct", _portfolio_cfg("max_short_exposure_pct", 0.30))
        self._max_drawdown_pct = p.get("max_drawdown_pct", _portfolio_cfg("max_drawdown_pct", 0.30))

    # Scout positions (low/medium) have caps. Core positions (high/highest) are uncapped —
    # Claude decides sizing within cash constraints.
    CONFIDENCE_CAPS = {
        "low": 0.05,
        "medium": 0.08,
        "high": 1.0,
        "highest": 1.0,
    }

    # Core positions (high/highest) get thesis-based exits, not mechanical stops
    CORE_CONFIDENCE = {"high", "highest"}

    @staticmethod
    def is_core_position(confidence: str) -> bool:
        """Returns True if this confidence tier is a core (conviction) position."""
        return confidence.lower() in RiskManagerV3.CORE_CONFIDENCE

    def evaluate_new_position(
        self,
        ticker: str,
        side: str,
        allocation_pct: float,
        price: float,
        portfolio_value: float,
        cash: float,
        open_position_count: int,
        existing_tickers: list[str],
        short_exposure: float = 0.0,
        thesis: str = "",
        dynamic_stop_pct: float | None = None,
        confidence: str = "medium",
        is_profitable: bool | None = None,
    ) -> V3PositionPlan | V3RiskVeto:
        """Validate and size a new position from Claude's allocation %."""
        side = side.upper()

        # Rule 1: Max positions
        if open_position_count >= self._max_positions:
            return V3RiskVeto(ticker, f"Max positions reached ({self._max_positions})")

        # Rule 2: No duplicate tickers
        if ticker in existing_tickers:
            return V3RiskVeto(ticker, f"Already holding {ticker}")

        # Rule 3: Cap allocation based on confidence tier
        max_for_confidence = self.CONFIDENCE_CAPS.get(confidence.lower(), 0.08)

        # Rule 3b: Profitability gate — unprofitable companies cannot get "highest"
        if is_profitable is False and confidence.lower() == "highest":
            max_for_confidence = self.CONFIDENCE_CAPS["high"]  # Cap at 10%
            logger.info(
                "  FUNDAMENTALS GATE: %s is unprofitable, capping confidence from highest to high (max 10%%)",
                ticker,
            )

        alloc = min(allocation_pct / 100.0, max_for_confidence)

        # Rule 4: Cash reserve (longs only)
        if side == "LONG":
            min_cash = portfolio_value * self._min_cash_pct
            available = cash - min_cash
            if available <= 0:
                return V3RiskVeto(ticker, "Cash reserve would be breached")
            max_from_cash = available
        else:
            max_from_cash = float("inf")

        # Rule 5: Short exposure limit
        if side == "SHORT":
            max_short = portfolio_value * self._max_short_pct
            available_short = max_short - short_exposure
            if available_short <= 0:
                return V3RiskVeto(ticker, "Max short exposure reached")
            max_from_cash = available_short

        # Calculate position value and shares
        target_value = portfolio_value * alloc
        position_value = min(target_value, max_from_cash)

        if position_value <= 0 or price <= 0:
            return V3RiskVeto(ticker, "Insufficient funds or invalid price")

        quantity = math.floor(position_value / price)
        if quantity < 1:
            return V3RiskVeto(ticker, "Position too small (< 1 share)")

        actual_value = quantity * price

        # Set catastrophic stop — use dynamic stop if provided, else default
        stop_pct = dynamic_stop_pct if dynamic_stop_pct is not None else self._catastrophic_stop_pct
        if side == "LONG":
            stop = round(price * (1 - stop_pct), 2)
        else:
            stop = round(price * (1 + stop_pct), 2)

        return V3PositionPlan(
            ticker=ticker,
            side=side,
            quantity=quantity,
            entry_price=price,
            catastrophic_stop=stop,
            allocation_pct=round(alloc * 100, 1),
            position_value=round(actual_value, 2),
            thesis_ref=thesis,
        )

    def evaluate_reduce(
        self,
        ticker: str,
        new_allocation_pct: float,
        current_qty: int,
        price: float,
        portfolio_value: float,
    ) -> int:
        """Calculate how many shares to sell for a position reduction.

        Returns number of shares to sell (0 if no reduction needed).
        """
        if portfolio_value <= 0 or price <= 0:
            return 0

        target_value = portfolio_value * (new_allocation_pct / 100.0)
        target_qty = math.floor(target_value / price)
        shares_to_sell = max(0, current_qty - target_qty)
        return shares_to_sell

    def check_drawdown(self, current_value: float, peak_value: float) -> bool:
        """Returns True if trading should continue, False if max drawdown hit."""
        if peak_value <= 0:
            return False
        drawdown = (peak_value - current_value) / peak_value
        return drawdown < self._max_drawdown_pct
