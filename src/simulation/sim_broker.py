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
    is_short: bool = False


@dataclass
class SimBroker:
    """A simulated broker that tracks positions in memory.

    Fills limit orders immediately at the requested price.
    Checks stop-loss and take-profit against daily high/low bars.
    Supports both long and short positions.
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
        position_value = 0.0
        for p in self.positions.values():
            if p.is_short:
                # Short liability: we received cash when opening, but owe shares back.
                # At entry price, the liability exactly offsets the cash received (net zero).
                # P&L is realized on close when buy-back price differs from entry.
                position_value -= p.quantity * p.entry_price
            else:
                position_value += p.quantity * p.entry_price
        return self.cash + position_value

    def place_bracket_order(self, plan: PositionPlan, is_short: bool = False, opened_at: str | None = None) -> OrderResult:
        """Simulates order fill at the plan's entry price."""
        cost = plan.quantity * plan.entry_price
        timestamp = opened_at or datetime.utcnow().isoformat()

        if is_short:
            # Short: we receive cash from selling shares we don't own
            self.cash += cost
            self.positions[plan.ticker] = SimPosition(
                ticker=plan.ticker,
                quantity=plan.quantity,
                entry_price=plan.entry_price,
                stop_loss=plan.stop_loss,
                take_profit=plan.take_profit,
                opened_at=timestamp,
                is_short=True,
            )
            logger.debug(
                "SIM: Shorted %d %s @ $%.2f (cash: $%.2f)",
                plan.quantity, plan.ticker, plan.entry_price, self.cash,
            )
        else:
            if cost > self.cash:
                return OrderResult(success=False, error="Insufficient cash")

            self.cash -= cost
            self.positions[plan.ticker] = SimPosition(
                ticker=plan.ticker,
                quantity=plan.quantity,
                entry_price=plan.entry_price,
                stop_loss=plan.stop_loss,
                take_profit=plan.take_profit,
                opened_at=timestamp,
                is_short=False,
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

        if pos.is_short:
            # Short P&L: profit when price drops
            pnl = (pos.entry_price - exit_price) * pos.quantity
            # Buy back shares to close — costs us money
            self.cash -= pos.quantity * exit_price
        else:
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
            "is_short": pos.is_short,
        })

        side = "SHORT" if pos.is_short else "LONG"
        logger.debug(
            "SIM: Closed %s %s @ $%.2f (P&L: $%.2f)",
            side, ticker, exit_price, pnl,
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

            if pos.is_short:
                # Short: stop-loss is ABOVE entry (triggered when price goes UP)
                # Take-profit is BELOW entry (triggered when price goes DOWN)
                if high >= pos.stop_loss:
                    tickers_to_close.append((ticker, pos.stop_loss, "stopped_out"))
                elif low <= pos.take_profit:
                    tickers_to_close.append((ticker, pos.take_profit, "take_profit"))
            else:
                # Long: normal stop/target check
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
                "is_short": p.is_short,
            }
            for p in self.positions.values()
        ]

    def get_short_exposure(self) -> float:
        """Total value of short positions."""
        return sum(
            p.quantity * p.entry_price
            for p in self.positions.values()
            if p.is_short
        )

    def update_position_prices(self, prices: dict[str, float]) -> None:
        """Update current prices for portfolio value calculation."""
        pass

    def get_account_snapshot(self) -> dict:
        return {
            "equity": self.portfolio_value,
            "cash": self.cash,
            "buying_power": self.cash,
            "portfolio_value": self.portfolio_value,
            "currency": "USD",
            "short_exposure": self.get_short_exposure(),
        }
