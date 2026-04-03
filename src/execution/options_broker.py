"""Live options broker using Alpaca Trading API.

Handles buy-to-open, sell-to-close, and position management for options.
Uses the same TradingClient as the equity broker.
"""
from __future__ import annotations

import logging

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, PositionIntent

from src.config import get_alpaca_keys
from src.execution.broker import OrderResult

logger = logging.getLogger(__name__)


class OptionsBroker:
    """Executes options orders via Alpaca."""

    def __init__(self, api_key: str | None = None, secret_key: str | None = None):
        if api_key and secret_key:
            self._api_key = api_key
            self._secret_key = secret_key
        else:
            self._api_key, self._secret_key = get_alpaca_keys()

        self._client = TradingClient(
            self._api_key, self._secret_key, paper=True,
        )

    def buy_to_open(self, contract_symbol: str, quantity: int) -> OrderResult:
        """Buy to open — long calls or long puts."""
        try:
            order = MarketOrderRequest(
                symbol=contract_symbol,
                qty=quantity,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            )
            result = self._client.submit_order(order)
            logger.info(
                "Options BTO: %d x %s [order_id: %s]",
                quantity, contract_symbol, result.id,
            )
            return OrderResult(
                success=True,
                order_id=str(result.id),
                filled_price=float(result.filled_avg_price) if result.filled_avg_price else None,
            )
        except Exception as e:
            logger.error("Options BTO failed for %s: %s", contract_symbol, e)
            return OrderResult(success=False, error=str(e))

    def sell_to_close(self, contract_symbol: str, quantity: int) -> OrderResult:
        """Sell to close — exit a long options position."""
        try:
            order = MarketOrderRequest(
                symbol=contract_symbol,
                qty=quantity,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                position_intent=PositionIntent.SELL_TO_CLOSE,
            )
            result = self._client.submit_order(order)
            logger.info(
                "Options STC: %d x %s [order_id: %s]",
                quantity, contract_symbol, result.id,
            )
            return OrderResult(
                success=True,
                order_id=str(result.id),
                filled_price=float(result.filled_avg_price) if result.filled_avg_price else None,
            )
        except Exception as e:
            logger.error("Options STC failed for %s: %s", contract_symbol, e)
            return OrderResult(success=False, error=str(e))

    def sell_to_open(self, contract_symbol: str, quantity: int) -> OrderResult:
        """Sell to open — cash-secured puts."""
        try:
            order = MarketOrderRequest(
                symbol=contract_symbol,
                qty=quantity,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                position_intent=PositionIntent.SELL_TO_OPEN,
            )
            result = self._client.submit_order(order)
            logger.info(
                "Options STO: %d x %s [order_id: %s]",
                quantity, contract_symbol, result.id,
            )
            return OrderResult(
                success=True,
                order_id=str(result.id),
                filled_price=float(result.filled_avg_price) if result.filled_avg_price else None,
            )
        except Exception as e:
            logger.error("Options STO failed for %s: %s", contract_symbol, e)
            return OrderResult(success=False, error=str(e))

    def close_position(self, contract_symbol: str) -> OrderResult:
        """Close an entire options position."""
        try:
            self._client.close_position(contract_symbol)
            logger.info("Options position closed: %s", contract_symbol)
            return OrderResult(success=True)
        except Exception as e:
            logger.error("Options close failed for %s: %s", contract_symbol, e)
            return OrderResult(success=False, error=str(e))

    def get_options_positions(self) -> list[dict]:
        """Get all open options positions."""
        try:
            all_positions = self._client.get_all_positions()
            options = []
            for p in all_positions:
                if hasattr(p, 'asset_class') and str(p.asset_class) == 'us_option':
                    options.append({
                        "symbol": p.symbol,
                        "qty": int(p.qty),
                        "avg_entry_price": float(p.avg_entry_price),
                        "current_price": float(p.current_price),
                        "market_value": float(p.market_value),
                        "unrealized_pl": float(p.unrealized_pl),
                        "unrealized_plpc": float(p.unrealized_plpc),
                        "side": str(p.side),
                    })
            return options
        except Exception as e:
            logger.error("Failed to get options positions: %s", e)
            return []
