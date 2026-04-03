"""Contract selector — picks the best options contract from a real chain.

Claude decides WHAT to trade (ticker, direction, conviction, action).
This module picks the specific contract from the live Alpaca chain:
strike, expiry, quantity — based on liquidity, DTE, and conviction rules.

Replaces the sim's synthetic strike selection with real market data.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from src.data.options_data import OptionsDataClient, OptionContract

logger = logging.getLogger(__name__)


@dataclass
class SelectedContract:
    """The selected contract ready for execution."""
    symbol: str  # OCC symbol
    underlying: str
    option_type: str  # "call" or "put"
    strike: float
    expiry: str
    premium: float  # Mid price per share
    quantity: int  # Number of contracts
    total_cost: float  # premium * 100 * quantity
    delta: float
    theta: float
    implied_volatility: float


class ContractSelector:
    """Selects the best contract from a real options chain."""

    def __init__(self, options_data: OptionsDataClient):
        self._data = options_data

    def select_contract(
        self,
        ticker: str,
        action: str,
        current_price: float,
        allocation_usd: float,
        strike_selection: str = "ATM",
        expiry_months: int = 6,
        min_dte: int = 45,
        max_dte: int = 90,
        min_open_interest: int = 100,
        max_bid_ask_spread_pct: float = 0.10,
    ) -> SelectedContract | None:
        """Select the best contract for a trade.

        Args:
            ticker: Underlying ticker.
            action: "BUY_CALL", "BUY_PUT", or "SELL_PUT".
            current_price: Current underlying price.
            allocation_usd: Dollar amount to allocate.
            strike_selection: "ATM", "5_OTM", "10_OTM", etc.
            expiry_months: Target months to expiry.
            min_dte: Minimum days to expiry for chain filter.
            max_dte: Maximum days to expiry for chain filter.
        """
        # Determine option type
        if action in ("BUY_CALL",):
            option_type = "call"
        elif action in ("BUY_PUT", "SELL_PUT"):
            option_type = "put"
        else:
            logger.error("Unknown option action: %s", action)
            return None

        # Calculate target strike
        target_strike = self._target_strike(current_price, strike_selection, option_type)

        # Fetch filtered chain from Alpaca
        chain = self._data.get_chain_for_entry(
            underlying=ticker,
            current_price=current_price,
            option_type=option_type,
            min_dte=min_dte,
            max_dte=max_dte,
            min_open_interest=min_open_interest,
            max_bid_ask_spread_pct=max_bid_ask_spread_pct,
        )

        if not chain:
            logger.warning("No liquid contracts found for %s %s", ticker, option_type)
            return None

        # Pick the best contract — closest to target strike
        best = min(chain, key=lambda c: abs(c.strike - target_strike))

        if best.mid <= 0:
            logger.warning("Best contract for %s has zero premium", ticker)
            return None

        # Calculate quantity
        quantity = self._calculate_quantity(best.mid, allocation_usd)
        if quantity < 1:
            logger.warning(
                "Allocation $%.0f too small for %s @ $%.2f premium",
                allocation_usd, ticker, best.mid,
            )
            return None

        total_cost = best.mid * 100 * quantity

        logger.info(
            "Selected %s: %s $%.0f %s exp %s @ $%.2f (%d contracts, $%.0f total, delta %.2f)",
            action, ticker, best.strike, best.option_type, best.expiry,
            best.mid, quantity, total_cost, best.delta,
        )

        return SelectedContract(
            symbol=best.symbol,
            underlying=ticker,
            option_type=best.option_type,
            strike=best.strike,
            expiry=best.expiry,
            premium=best.mid,
            quantity=quantity,
            total_cost=round(total_cost, 2),
            delta=best.delta,
            theta=best.theta,
            implied_volatility=best.implied_volatility,
        )

    @staticmethod
    def _calculate_quantity(premium_per_share: float, allocation_usd: float) -> int:
        """Calculate number of contracts. Each contract = 100 shares."""
        if premium_per_share <= 0:
            return 0
        cost_per_contract = premium_per_share * 100
        return max(0, math.floor(allocation_usd / cost_per_contract))

    @staticmethod
    def _target_strike(
        price: float, selection: str, option_type: str,
    ) -> float:
        """Calculate target strike price from selection string."""
        if selection == "ATM":
            return price

        pct = 0.0
        if "5" in selection:
            pct = 0.05
        elif "10" in selection:
            pct = 0.10
        elif "15" in selection:
            pct = 0.15

        is_otm = "OTM" in selection.upper()

        if option_type == "call":
            return price * (1 + pct) if is_otm else price * (1 - pct)
        else:
            return price * (1 - pct) if is_otm else price * (1 + pct)
