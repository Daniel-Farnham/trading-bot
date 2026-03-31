from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from src.execution.broker import OrderResult
from src.options.pricing import (
    price_option, greeks, time_to_expiry_years, DEFAULT_RISK_FREE_RATE,
)
from src.strategy.risk_v3 import PositionPlan

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
    current_price: float = 0.0  # Updated daily with market close


@dataclass
class SimOptionPosition:
    """A simulated option contract position."""
    contract_id: str         # e.g., NVDA_250620C140
    ticker: str              # Underlying
    option_type: str         # "CALL" or "PUT"
    strike: float
    expiry: str              # "2025-06-20"
    quantity: int            # Number of contracts (each = 100 shares)
    premium_paid: float      # Per-share premium at entry
    entry_date: str
    is_short: bool = False   # True = sold/written
    current_premium: float = 0.0
    current_delta: float = 0.0
    sigma: float = 0.30      # Volatility used for pricing


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
    option_positions: dict[str, SimOptionPosition] = field(default_factory=dict)
    closed_trades: list[dict] = field(default_factory=list)
    total_pnl: float = 0.0

    def __post_init__(self):
        if self.cash == 0.0:
            self.cash = self.initial_cash

    @property
    def portfolio_value(self) -> float:
        # Equity positions
        position_value = 0.0
        for p in self.positions.values():
            price = p.current_price if p.current_price > 0 else p.entry_price
            if p.is_short:
                position_value += p.quantity * (p.entry_price - price)
            else:
                position_value += p.quantity * price
        # Options mark-to-market
        options_value = 0.0
        for opt in self.option_positions.values():
            contract_value = opt.current_premium * 100 * opt.quantity
            if opt.is_short:
                # Short options: we owe the current premium
                options_value -= contract_value
            else:
                options_value += contract_value
        return self.cash + position_value + options_value

    def update_prices(self, daily_bars: dict[str, dict]) -> None:
        """Update current prices for all positions from daily bars."""
        for ticker, pos in self.positions.items():
            bar = daily_bars.get(ticker)
            if bar:
                pos.current_price = bar["close"]

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

    def add_to_position(self, ticker: str, quantity: int, price: float) -> OrderResult:
        """Add shares to an existing position at a new price (pyramiding).

        Calculates new weighted average entry price.
        """
        pos = self.positions.get(ticker)
        if not pos:
            return OrderResult(success=False, error=f"No existing position for {ticker}")

        cost = quantity * price
        if not pos.is_short and cost > self.cash:
            return OrderResult(success=False, error="Insufficient cash for pyramid")

        # Calculate weighted average entry price
        old_cost = pos.quantity * pos.entry_price
        new_cost = quantity * price
        new_qty = pos.quantity + quantity
        avg_price = (old_cost + new_cost) / new_qty

        if pos.is_short:
            self.cash += cost  # Receive cash from shorting more
        else:
            self.cash -= cost

        pos.quantity = new_qty
        pos.entry_price = round(avg_price, 2)

        logger.debug(
            "SIM: Added %d %s @ $%.2f (avg entry now $%.2f, total %d shares)",
            quantity, ticker, price, avg_price, new_qty,
        )
        return OrderResult(
            success=True,
            order_id=f"sim_add_{ticker}_{datetime.utcnow().timestamp():.0f}",
            filled_price=price,
        )

    def update_stops(self, ticker: str, stop_loss: float, take_profit: float) -> bool:
        """Update stop/target on an existing position (e.g., scout → core upgrade)."""
        pos = self.positions.get(ticker)
        if not pos:
            return False
        pos.stop_loss = stop_loss
        pos.take_profit = take_profit
        return True

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

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------

    def place_option_order(
        self, contract_id: str, ticker: str, option_type: str,
        strike: float, expiry: str, quantity: int, premium: float,
        is_short: bool, entry_date: str, sigma: float = 0.30,
    ) -> OrderResult:
        """Place an option order. Premium is per share (x100 per contract)."""
        total_cost = premium * 100 * quantity

        if is_short:
            # Selling options: receive premium, but must reserve cash for assignment
            if option_type.upper() == "PUT":
                # Cash-secured put: reserve strike * 100 * qty
                assignment_reserve = strike * 100 * quantity
                if assignment_reserve > self.cash:
                    return OrderResult(success=False, error="Insufficient cash for cash-secured put")
            self.cash += total_cost
        else:
            # Buying options: pay premium
            if total_cost > self.cash:
                return OrderResult(success=False, error="Insufficient cash for option premium")
            self.cash -= total_cost

        self.option_positions[contract_id] = SimOptionPosition(
            contract_id=contract_id,
            ticker=ticker,
            option_type=option_type.upper(),
            strike=strike,
            expiry=expiry,
            quantity=quantity,
            premium_paid=premium,
            entry_date=entry_date,
            is_short=is_short,
            current_premium=premium,
            sigma=sigma,
        )

        action = "Sold" if is_short else "Bought"
        logger.debug(
            "SIM: %s %d %s %s $%.0f %s @ $%.2f/sh ($%.0f total)",
            action, quantity, ticker, option_type, strike, expiry, premium, total_cost,
        )
        return OrderResult(success=True, order_id=f"sim_opt_{contract_id}")

    def close_option_position(self, contract_id: str, current_premium: float) -> OrderResult:
        """Close an option position at the current premium."""
        if contract_id not in self.option_positions:
            return OrderResult(success=False, error=f"No option position {contract_id}")

        opt = self.option_positions.pop(contract_id)
        total_exit = current_premium * 100 * opt.quantity

        if opt.is_short:
            # Buying back a short option costs money
            self.cash -= total_exit
            pnl = (opt.premium_paid - current_premium) * 100 * opt.quantity
        else:
            # Selling a long option receives money
            self.cash += total_exit
            pnl = (current_premium - opt.premium_paid) * 100 * opt.quantity

        self.total_pnl += pnl
        self.closed_trades.append({
            "ticker": opt.ticker,
            "contract_id": contract_id,
            "instrument": "OPTION",
            "option_type": opt.option_type,
            "strike": opt.strike,
            "expiry": opt.expiry,
            "quantity": opt.quantity,
            "entry_premium": opt.premium_paid,
            "exit_premium": current_premium,
            "pnl": round(pnl, 2),
            "is_short": opt.is_short,
        })
        return OrderResult(success=True, filled_price=current_premium)

    def reprice_options(self, daily_bars: dict, current_date: str) -> None:
        """Reprice all option positions using Black-Scholes."""
        for opt in self.option_positions.values():
            bar = daily_bars.get(opt.ticker)
            if not bar:
                continue
            S = bar["close"]
            T = time_to_expiry_years(current_date, opt.expiry)
            if T <= 0:
                # At expiry — set to intrinsic value
                if opt.option_type == "CALL":
                    opt.current_premium = max(0.0, S - opt.strike)
                else:
                    opt.current_premium = max(0.0, opt.strike - S)
                opt.current_delta = 1.0 if opt.current_premium > 0 else 0.0
            else:
                opt.current_premium = price_option(
                    S, opt.strike, T, DEFAULT_RISK_FREE_RATE, opt.sigma, opt.option_type,
                )
                g = greeks(S, opt.strike, T, DEFAULT_RISK_FREE_RATE, opt.sigma, opt.option_type)
                opt.current_delta = g.delta

    def check_option_expiry(self, current_date: str, daily_bars: dict) -> list[dict]:
        """Handle option expiration. Returns list of expired/exercised trades."""
        expired = []
        to_remove = []

        for contract_id, opt in self.option_positions.items():
            if current_date < opt.expiry:
                continue

            bar = daily_bars.get(opt.ticker)
            S = bar["close"] if bar else 0.0

            # Determine if ITM
            if opt.option_type == "CALL":
                intrinsic = max(0.0, S - opt.strike)
            else:
                intrinsic = max(0.0, opt.strike - S)

            itm = intrinsic > 0

            if itm and not opt.is_short:
                # Long ITM option — exercise (close at intrinsic value)
                pnl = (intrinsic - opt.premium_paid) * 100 * opt.quantity
                self.cash += intrinsic * 100 * opt.quantity
                logger.info("  OPTION EXERCISED: %s ITM @ $%.2f (P&L: $%+.2f)", contract_id, intrinsic, pnl)
            elif itm and opt.is_short:
                # Short ITM option — assigned
                pnl = (opt.premium_paid - intrinsic) * 100 * opt.quantity
                self.cash -= intrinsic * 100 * opt.quantity
                logger.info("  OPTION ASSIGNED: %s ITM @ $%.2f (P&L: $%+.2f)", contract_id, intrinsic, pnl)
            else:
                # OTM — expires worthless
                if opt.is_short:
                    pnl = opt.premium_paid * 100 * opt.quantity  # Keep full premium
                    logger.info("  OPTION EXPIRED WORTHLESS: %s (kept $%.2f premium)", contract_id, pnl)
                else:
                    pnl = -opt.premium_paid * 100 * opt.quantity  # Lost full premium
                    logger.info("  OPTION EXPIRED WORTHLESS: %s (lost $%.2f)", contract_id, -pnl)

            self.total_pnl += pnl
            self.closed_trades.append({
                "ticker": opt.ticker,
                "contract_id": contract_id,
                "instrument": "OPTION",
                "option_type": opt.option_type,
                "strike": opt.strike,
                "expiry": opt.expiry,
                "quantity": opt.quantity,
                "entry_premium": opt.premium_paid,
                "exit_premium": intrinsic if itm else 0.0,
                "pnl": round(pnl, 2),
                "is_short": opt.is_short,
                "exit_reason": "exercised" if itm else "expired_worthless",
            })
            expired.append(self.closed_trades[-1])
            to_remove.append(contract_id)

        for cid in to_remove:
            del self.option_positions[cid]

        return expired

    def get_portfolio_greeks(self) -> dict:
        """Aggregate portfolio Greeks from all option positions."""
        net_delta = 0.0
        net_theta = 0.0
        total_premium = 0.0
        for opt in self.option_positions.values():
            sign = -1 if opt.is_short else 1
            net_delta += sign * opt.current_delta * 100 * opt.quantity
            total_premium += opt.current_premium * 100 * opt.quantity
        return {
            "net_delta": round(net_delta, 1),
            "total_options_value": round(total_premium, 2),
            "option_count": len(self.option_positions),
        }

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
