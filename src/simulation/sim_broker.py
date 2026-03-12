from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from src.execution.broker import OrderResult
from src.strategy.risk import PositionPlan

logger = logging.getLogger(__name__)


@dataclass
class SimPosition:
    ticker: str
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: float
    opened_at: str


@dataclass
class SimBroker:
    """A simulated broker that tracks positions in memory.

    Fills limit orders immediately at the requested price.
    Checks stop-loss and take-profit against daily high/low bars.
    """
    initial_cash: float = 100000.0
    cash: float = 0.0
    positions: dict[str, SimPosition] = field(default_factory=dict)
    closed_trades: list[dict] = field(default_factory=list)
    total_pnl: float = 0.0

    def __post_init__(self):
        if self.cash == 0.0:
            self.cash = self.initial_cash

    @property
    def portfolio_value(self) -> float:
        # Approximate: cash + sum of position values at entry
        # In practice, update_prices gives us real values
        position_value = sum(
            p.quantity * p.entry_price for p in self.positions.values()
        )
        return self.cash + position_value

    def place_bracket_order(self, plan: PositionPlan) -> OrderResult:
        """Simulates order fill at the plan's entry price."""
        cost = plan.quantity * plan.entry_price

        if cost > self.cash:
            return OrderResult(success=False, error="Insufficient cash")

        self.cash -= cost
        self.positions[plan.ticker] = SimPosition(
            ticker=plan.ticker,
            quantity=plan.quantity,
            entry_price=plan.entry_price,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            opened_at=datetime.utcnow().isoformat(),
        )

        logger.debug(
            "SIM: Bought %d %s @ $%.2f (cash remaining: $%.2f)",
            plan.quantity, plan.ticker, plan.entry_price, self.cash,
        )

        return OrderResult(
            success=True,
            order_id=f"sim_{plan.ticker}_{datetime.utcnow().timestamp():.0f}",
            filled_price=plan.entry_price,
        )

    def close_position(self, ticker: str, price: float | None = None) -> OrderResult:
        """Closes a position at the given price (or entry price if not provided)."""
        if ticker not in self.positions:
            return OrderResult(success=False, error=f"No position in {ticker}")

        pos = self.positions.pop(ticker)
        exit_price = price or pos.entry_price
        pnl = (exit_price - pos.entry_price) * pos.quantity
        self.cash += pos.quantity * exit_price
        self.total_pnl += pnl

        self.closed_trades.append({
            "ticker": ticker,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "stop_loss": pos.stop_loss,
            "take_profit": pos.take_profit,
        })

        logger.debug(
            "SIM: Closed %s @ $%.2f (P&L: $%.2f)",
            ticker, exit_price, pnl,
        )

        return OrderResult(success=True, filled_price=exit_price)

    def check_stops_and_targets(self, daily_bars: dict[str, dict]) -> list[dict]:
        """Check all positions against daily high/low for stop-loss/take-profit triggers.

        daily_bars: {ticker: {"high": float, "low": float, "close": float}}
        Returns list of closed trade dicts.
        """
        triggered = []
        tickers_to_close = []

        for ticker, pos in self.positions.items():
            bar = daily_bars.get(ticker)
            if not bar:
                continue

            low = bar["low"]
            high = bar["high"]

            if low <= pos.stop_loss:
                tickers_to_close.append((ticker, pos.stop_loss, "stopped_out"))
            elif high >= pos.take_profit:
                tickers_to_close.append((ticker, pos.take_profit, "take_profit"))

        for ticker, price, reason in tickers_to_close:
            result = self.close_position(ticker, price)
            if result.success:
                trade = self.closed_trades[-1]
                trade["exit_reason"] = reason
                triggered.append(trade)

        return triggered

    def get_positions_list(self) -> list[dict]:
        """Returns positions in the same format as MarketData.get_positions."""
        return [
            {
                "ticker": p.ticker,
                "qty": p.quantity,
                "avg_entry": p.entry_price,
                "current_price": p.entry_price,  # Updated by simulation engine
                "market_value": p.quantity * p.entry_price,
                "unrealized_pnl": 0.0,
                "unrealized_pnl_pct": 0.0,
            }
            for p in self.positions.values()
        ]

    def update_position_prices(self, prices: dict[str, float]) -> None:
        """Update current prices for portfolio value calculation."""
        # Positions are stored with entry price; this is just for reporting
        pass

    def get_account_snapshot(self) -> dict:
        return {
            "equity": self.portfolio_value,
            "cash": self.cash,
            "buying_power": self.cash,
            "portfolio_value": self.portfolio_value,
            "currency": "USD",
        }
