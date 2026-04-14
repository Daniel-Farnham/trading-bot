from __future__ import annotations

import logging
from dataclasses import dataclass

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    ClosePositionRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

from src.config import get_alpaca_keys
from src.storage.models import TradeSide
from src.strategy.risk_v3 import PositionPlan

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_id: str | None = None
    filled_price: float | None = None
    error: str | None = None


class Broker:
    def __init__(self, api_key: str | None = None, secret_key: str | None = None):
        if api_key and secret_key:
            self._api_key = api_key
            self._secret_key = secret_key
        else:
            self._api_key, self._secret_key = get_alpaca_keys()

        self._client = TradingClient(
            self._api_key, self._secret_key, paper=True
        )

    def _get_time_in_force(self) -> TimeInForce:
        """DAY if market is open, GTC (good-til-cancelled) if closed."""
        try:
            clock = self._client.get_clock()
            if clock.is_open:
                return TimeInForce.DAY
            logger.info("Market closed — queuing order as GTC (good-til-cancelled)")
            return TimeInForce.GTC
        except Exception:
            return TimeInForce.DAY

    def place_bracket_order(self, plan: PositionPlan) -> OrderResult:
        """Places a bracket order: limit entry + stop-loss + take-profit."""
        try:
            order = LimitOrderRequest(
                symbol=plan.ticker,
                qty=plan.quantity,
                side=OrderSide.BUY,
                type="limit",
                time_in_force=TimeInForce.DAY,
                limit_price=round(plan.entry_price, 2),
                order_class=OrderClass.BRACKET,
                stop_loss={"stop_price": round(plan.stop_loss, 2)},
                take_profit={"limit_price": round(plan.take_profit, 2)},
            )

            result = self._client.submit_order(order)

            logger.info(
                "Bracket order placed: %s %d shares of %s @ $%.2f "
                "(SL: $%.2f, TP: $%.2f) [order_id: %s]",
                "BUY", plan.quantity, plan.ticker, plan.entry_price,
                plan.stop_loss, plan.take_profit, result.id,
            )

            return OrderResult(
                success=True,
                order_id=str(result.id),
                filled_price=float(result.filled_avg_price) if result.filled_avg_price else None,
            )

        except Exception as e:
            logger.error("Order failed for %s: %s", plan.ticker, str(e))
            return OrderResult(success=False, error=str(e))

    def place_market_sell(self, ticker: str, quantity: int) -> OrderResult:
        """Places a market sell order to exit a position."""
        try:
            order = MarketOrderRequest(
                symbol=ticker,
                qty=quantity,
                side=OrderSide.SELL,
                time_in_force=self._get_time_in_force(),
            )

            result = self._client.submit_order(order)

            logger.info(
                "Market sell placed: %d shares of %s [order_id: %s]",
                quantity, ticker, result.id,
            )

            return OrderResult(
                success=True,
                order_id=str(result.id),
                filled_price=float(result.filled_avg_price) if result.filled_avg_price else None,
            )

        except Exception as e:
            logger.error("Sell order failed for %s: %s", ticker, str(e))
            return OrderResult(success=False, error=str(e))

    def close_position(self, ticker: str) -> OrderResult:
        """Closes an entire position for a ticker."""
        try:
            self._client.close_position(ticker)

            logger.info("Position closed: %s", ticker)
            return OrderResult(success=True)

        except Exception as e:
            logger.error("Close position failed for %s: %s", ticker, str(e))
            return OrderResult(success=False, error=str(e))

    def cancel_all_orders(self) -> bool:
        """Cancels all open orders. Returns True if successful."""
        try:
            self._client.cancel_orders()
            logger.info("All open orders cancelled")
            return True
        except Exception as e:
            logger.error("Cancel all orders failed: %s", str(e))
            return False

    def place_market_buy(self, ticker: str, quantity: int) -> OrderResult:
        """Places a market buy order for core position entries."""
        try:
            order = MarketOrderRequest(
                symbol=ticker,
                qty=quantity,
                side=OrderSide.BUY,
                time_in_force=self._get_time_in_force(),
            )

            result = self._client.submit_order(order)

            logger.info(
                "Market buy placed: %d shares of %s [order_id: %s]",
                quantity, ticker, result.id,
            )

            return OrderResult(
                success=True,
                order_id=str(result.id),
                filled_price=float(result.filled_avg_price) if result.filled_avg_price else None,
            )

        except Exception as e:
            logger.error("Buy order failed for %s: %s", ticker, str(e))
            return OrderResult(success=False, error=str(e))

    def place_short_sell(self, ticker: str, quantity: int) -> OrderResult:
        """Places a market short sell order."""
        try:
            order = MarketOrderRequest(
                symbol=ticker,
                qty=quantity,
                side=OrderSide.SELL,
                time_in_force=self._get_time_in_force(),
            )

            result = self._client.submit_order(order)

            logger.info(
                "Short sell placed: %d shares of %s [order_id: %s]",
                quantity, ticker, result.id,
            )

            return OrderResult(
                success=True,
                order_id=str(result.id),
                filled_price=float(result.filled_avg_price) if result.filled_avg_price else None,
            )

        except Exception as e:
            logger.error("Short sell failed for %s: %s", ticker, str(e))
            return OrderResult(success=False, error=str(e))

    def get_all_orders(self, status: str = "open") -> list[dict]:
        """List orders by status."""
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            status_map = {
                "open": QueryOrderStatus.OPEN,
                "closed": QueryOrderStatus.CLOSED,
                "all": QueryOrderStatus.ALL,
            }
            request = GetOrdersRequest(status=status_map.get(status, QueryOrderStatus.OPEN))
            orders = self._client.get_orders(filter=request)
            # Same enum-normalisation as get_order — raw str() on an
            # alpaca enum gives "OrderStatus.NEW" which breaks every
            # downstream comparison.
            def _val(x):
                return x.value if hasattr(x, "value") else str(x)
            return [
                {
                    "id": str(o.id),
                    "status": _val(o.status),
                    "symbol": o.symbol,
                    "qty": str(o.qty),
                    "filled_qty": str(o.filled_qty),
                    "side": _val(o.side),
                    "type": _val(o.type),
                }
                for o in orders
            ]
        except Exception as e:
            logger.error("Failed to get orders: %s", str(e))
            return []

    def get_order(self, order_id: str) -> dict | None:
        """Gets the current state of an order.

        Normalises Alpaca enum fields (OrderStatus, OrderSide, OrderType)
        down to their underlying string values. The earlier version used
        str(order.status) which produces "OrderStatus.FILLED" for an enum
        — the reconciler then compared lowercased "orderstatus.filled"
        against {"filled"} and never matched, so filled orders were
        silently stranded in pending_orders.json forever.
        """
        def _val(x):
            # Alpaca's enums subclass str but __str__ returns "Class.MEMBER".
            # .value gives the underlying string ("filled", "buy", "market").
            return x.value if hasattr(x, "value") else str(x)
        try:
            order = self._client.get_order_by_id(order_id)
            return {
                "id": str(order.id),
                "status": _val(order.status),
                "symbol": order.symbol,
                "qty": str(order.qty),
                "filled_qty": str(order.filled_qty),
                "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
                "side": _val(order.side),
                "type": _val(order.type),
            }
        except Exception:
            return None
