"""Reconciliation manager — keeps internal state in sync with Alpaca.

Runs during every 30-min trigger check:
1. Check pending orders: filled → update ledger, expired → retry
2. Sync portfolio_ledger.md from Alpaca positions (source of truth)
3. Clean up theses for permanently failed orders
"""
from __future__ import annotations

import logging
from datetime import date

from src.data.market import MarketData
from src.execution.broker import Broker, OrderResult
from src.live.pending_orders import PendingOrderTracker, PendingOrder
from src.strategy.thesis_manager import ThesisManager

logger = logging.getLogger(__name__)

# Alpaca order statuses
FILLED_STATUSES = {"filled"}
RETRYABLE_STATUSES = {"expired"}  # Only retry expired — cancelled orders are intentional
TERMINAL_NO_RETRY_STATUSES = {"canceled", "cancelled", "suspended", "rejected"}
PARTIAL_STATUSES = {"partially_filled"}


class ReconcileManager:
    def __init__(
        self,
        broker: Broker,
        market_data: MarketData,
        thesis_manager: ThesisManager,
        pending_tracker: PendingOrderTracker,
    ):
        self._broker = broker
        self._market = market_data
        self._tm = thesis_manager
        self._pending = pending_tracker

    def reconcile(self) -> dict:
        """Run full reconciliation. Returns summary of actions taken."""
        summary = {
            "orders_filled": [],
            "orders_retried": [],
            "orders_failed": [],
            "ledger_synced": False,
            "positions_added": [],
            "positions_removed": [],
        }

        # Step 1: Check pending orders
        self._reconcile_pending_orders(summary)

        # Step 2: Sync ledger from Alpaca
        self._sync_ledger_from_alpaca(summary)

        if any(summary[k] for k in summary if k != "ledger_synced"):
            logger.info("Reconciliation summary: %s", summary)

        return summary

    def _reconcile_pending_orders(self, summary: dict) -> None:
        """Check each pending order against Alpaca and handle accordingly."""
        pending = self._pending.get_all()
        if not pending:
            return

        logger.info("Checking %d pending orders...", len(pending))

        for order in list(pending):  # copy since we modify during iteration
            alpaca_order = self._broker.get_order(order.order_id)

            if not alpaca_order:
                logger.warning("Could not fetch order %s for %s", order.order_id, order.ticker)
                continue

            status = alpaca_order.get("status", "").lower()
            filled_qty = int(float(alpaca_order.get("filled_qty", 0) or 0))
            total_qty = int(float(alpaca_order.get("qty", 0) or 0))

            if status in FILLED_STATUSES:
                self._handle_filled(order, alpaca_order, summary)
            elif status in RETRYABLE_STATUSES:
                if filled_qty > 0 and filled_qty < total_qty:
                    self._handle_partial_then_expired(order, alpaca_order, filled_qty, total_qty, summary)
                else:
                    self._handle_expired(order, summary)
            elif status in TERMINAL_NO_RETRY_STATUSES:
                # Cancelled/rejected — don't retry, just clean up
                logger.info(
                    "ORDER %s: %s %d %s — removing from tracking (no retry)",
                    status.upper(), order.action, order.qty, order.ticker,
                )
                self._pending.remove(order.order_id)
                summary["orders_failed"].append({
                    "ticker": order.ticker, "action": order.action,
                    "qty": order.qty, "reason": status,
                })
            elif filled_qty > 0 and filled_qty < total_qty:
                # Still open but partially filled — just log, keep tracking
                logger.info(
                    "Order %s for %s partially filled: %d/%d",
                    order.order_id, order.ticker, filled_qty, total_qty,
                )
            # else: still open, no fills yet — keep tracking

    def _handle_filled(self, order: PendingOrder, alpaca_order: dict, summary: dict) -> None:
        """Order fully filled — remove from pending, log success."""
        fill_price = alpaca_order.get("filled_avg_price", 0)
        logger.info(
            "ORDER FILLED: %s %d %s @ $%.2f",
            order.action, order.qty, order.ticker, fill_price or 0,
        )
        self._pending.remove(order.order_id)
        summary["orders_filled"].append({
            "ticker": order.ticker,
            "action": order.action,
            "qty": order.qty,
            "fill_price": fill_price,
        })

    def _handle_partial_then_expired(
        self,
        order: PendingOrder,
        alpaca_order: dict,
        filled_qty: int,
        total_qty: int,
        summary: dict,
    ) -> None:
        """Order partially filled then expired — retry the unfilled remainder."""
        fill_price = alpaca_order.get("filled_avg_price", 0)
        remaining = total_qty - filled_qty
        logger.info(
            "ORDER PARTIAL+EXPIRED: %s %d/%d %s filled @ $%.2f — retrying %d shares",
            order.action, filled_qty, total_qty, order.ticker, fill_price or 0, remaining,
        )

        summary["orders_filled"].append({
            "ticker": order.ticker,
            "action": order.action,
            "qty": filled_qty,
            "fill_price": fill_price,
            "partial": True,
        })

        # Retry the remainder
        self._retry_order(order, remaining, summary)

    def _handle_expired(self, order: PendingOrder, summary: dict) -> None:
        """Order expired/cancelled with zero fills — retry or give up."""
        if order.can_retry:
            logger.info(
                "ORDER EXPIRED: %s %d %s — retrying (attempt %d/%d)",
                order.action, order.qty, order.ticker,
                order.retry_count + 1, 3,
            )
            self._retry_order(order, order.qty, summary)
        else:
            logger.warning(
                "ORDER PERMANENTLY FAILED: %s %d %s — max retries exhausted",
                order.action, order.qty, order.ticker,
            )
            self._pending.remove(order.order_id)

            # Clean up thesis if no position exists in Alpaca
            alpaca_pos = self._market.get_position(order.ticker)
            if not alpaca_pos:
                self._tm.remove_position(order.ticker)
                self._tm.move_to_watching(
                    order.ticker, exit_price=0,
                    reason=f"Order failed after {order.retry_count} retries — never filled",
                )
                logger.info("Cleaned up thesis for %s — moved to WATCHING", order.ticker)

            summary["orders_failed"].append({
                "ticker": order.ticker,
                "action": order.action,
                "qty": order.qty,
                "retries": order.retry_count,
            })

    def _retry_order(self, order: PendingOrder, qty: int, summary: dict) -> None:
        """Resubmit an order for the given quantity."""
        action = order.action.upper()

        if action in ("SHORT",):
            result = self._broker.place_short_sell(order.ticker, qty)
        elif action in ("BUY (CORE)", "BUY (SCOUT)", "BUY", "PYRAMID"):
            result = self._broker.place_market_buy(order.ticker, qty)
        else:
            # Options or unknown — don't retry automatically
            logger.warning("Cannot auto-retry %s order for %s", action, order.ticker)
            self._pending.remove(order.order_id)
            summary["orders_failed"].append({
                "ticker": order.ticker, "action": action, "qty": qty,
                "reason": "unsupported action for retry",
            })
            return

        if result.success and result.order_id:
            self._pending.record_retry(order.order_id, result.order_id)
            summary["orders_retried"].append({
                "ticker": order.ticker,
                "action": action,
                "qty": qty,
                "new_order_id": result.order_id,
            })
        else:
            logger.error("Retry failed for %s: %s", order.ticker, result.error)
            # Keep tracking — will retry next cycle if retries remain
            self._pending.update_status(order.order_id, "retry_failed")

    def _sync_ledger_from_alpaca(self, summary: dict) -> None:
        """Rebuild portfolio_ledger.md from Alpaca positions (source of truth)."""
        try:
            positions = self._market.get_positions()
        except Exception as e:
            logger.error("Failed to fetch Alpaca positions for ledger sync: %s", e)
            return

        alpaca_tickers = {p["ticker"] for p in positions}
        memory_holdings = self._tm.get_holdings()
        memory_tickers = {h["ticker"] for h in memory_holdings}

        # Build updated holdings from Alpaca data, preserving date_opened from memory
        memory_dates = {h["ticker"]: h.get("date_opened", date.today().isoformat()) for h in memory_holdings}

        updated_holdings = []
        for p in positions:
            ticker = p["ticker"]
            qty = p["qty"]
            entry_price = p["avg_entry"]
            current_price = p["current_price"]
            market_value = p["market_value"]

            updated_holdings.append({
                "ticker": ticker,
                "side": "SHORT" if qty < 0 else "LONG",
                "qty": abs(qty),
                "entry_price": entry_price,
                "current_value": market_value,
                "current_price": current_price,
                "date_opened": memory_dates.get(ticker, date.today().isoformat()),
            })

        # Log differences
        added = alpaca_tickers - memory_tickers
        # Don't count tickers with pending orders as "removed" — they're still being filled
        pending_tickers = {o.ticker for o in self._pending.get_all()}
        removed = memory_tickers - alpaca_tickers - pending_tickers

        for ticker in added:
            logger.info("LEDGER SYNC: Added %s (in Alpaca, was missing from memory)", ticker)
            summary["positions_added"].append(ticker)
        for ticker in removed:
            logger.info("LEDGER SYNC: Removed %s (not in Alpaca, was stale in memory)", ticker)
            summary["positions_removed"].append(ticker)

        # Rebuild ledger
        self._tm._rebuild_ledger(updated_holdings)
        summary["ledger_synced"] = True
