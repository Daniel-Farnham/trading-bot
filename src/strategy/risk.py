from __future__ import annotations

import math
from dataclasses import dataclass

from src.config import CONFIG
from src.storage.models import Signal


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


@dataclass
class RiskVeto:
    """Reason a trade was rejected by risk management."""
    reason: str


class RiskManager:
    def __init__(self, params: dict | None = None):
        self._params = params or CONFIG.get("trading", {})

    def evaluate(
        self,
        signal: Signal,
        portfolio_value: float,
        cash: float,
        open_position_count: int,
        existing_ticker_positions: list[str],
    ) -> PositionPlan | RiskVeto:
        """Validate a signal against risk rules and return a position plan or veto."""

        # Rule 1: Max open positions
        max_positions = self._params.get("max_open_positions", 10)
        if open_position_count >= max_positions:
            return RiskVeto(
                reason=f"Max open positions reached ({max_positions})"
            )

        # Rule 2: Don't double up on existing positions
        if signal.ticker in existing_ticker_positions:
            return RiskVeto(
                reason=f"Already holding position in {signal.ticker}"
            )

        # Rule 3: Min cash reserve
        min_cash_pct = self._params.get("min_cash_reserve_pct", 0.20)
        min_cash = portfolio_value * min_cash_pct
        available_cash = cash - min_cash
        if available_cash <= 0:
            return RiskVeto(reason="Cash reserve would be breached")

        # Rule 4: Max position size
        max_position_pct = self._params.get("max_position_pct", 0.10)
        max_position_value = portfolio_value * max_position_pct

        # Scale position by confidence: higher confidence = larger position
        scaled_position_value = max_position_value * signal.confidence
        position_value = min(scaled_position_value, available_cash)

        if position_value <= 0 or signal.current_price <= 0:
            return RiskVeto(reason="Insufficient funds for minimum position")

        # Calculate quantity
        quantity = math.floor(position_value / signal.current_price)
        if quantity < 1:
            return RiskVeto(reason="Position too small (less than 1 share)")

        # Actual position value and risk
        actual_value = quantity * signal.current_price
        risk_per_share = signal.current_price - signal.stop_loss
        risk_amount = quantity * risk_per_share
        risk_pct = risk_amount / portfolio_value if portfolio_value > 0 else 0

        return PositionPlan(
            ticker=signal.ticker,
            quantity=quantity,
            entry_price=signal.current_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_amount=round(risk_amount, 2),
            position_value=round(actual_value, 2),
            risk_pct=round(risk_pct, 4),
        )

    def check_daily_loss(
        self, daily_pnl: float, portfolio_value: float
    ) -> bool:
        """Returns True if trading should continue, False if daily loss limit hit."""
        max_daily_loss_pct = self._params.get("max_daily_loss_pct", 0.03)
        if portfolio_value <= 0:
            return False
        loss_pct = abs(daily_pnl) / portfolio_value if daily_pnl < 0 else 0
        return loss_pct < max_daily_loss_pct

    def check_drawdown(
        self, current_value: float, peak_value: float
    ) -> bool:
        """Returns True if trading should continue, False if max drawdown hit."""
        max_drawdown_pct = self._params.get("max_drawdown_pct", 0.15)
        if peak_value <= 0:
            return False
        drawdown = (peak_value - current_value) / peak_value
        return drawdown < max_drawdown_pct
