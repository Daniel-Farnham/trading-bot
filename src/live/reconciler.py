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

    # Actions whose memory writes are deferred until the broker confirms
    # a fill. The orchestrator strips these out of _apply_to_memory on
    # submit; the reconciler writes them here once Alpaca says they filled.
    NEW_POSITION_ACTIONS = {"BUY (CORE)", "BUY (SCOUT)", "SHORT"}

    def _handle_filled(self, order: PendingOrder, alpaca_order: dict, summary: dict) -> None:
        """Order fully filled — remove from pending, log success.

        Deferred memory writes happen here so an order that queues GTC over
        the weekend and later cancels/fails never leaves a phantom thesis
        or a fictional journal entry behind.

        - PYRAMID      → append [PYRAMID] note + journal entry
        - BUY (CORE/SCOUT) / SHORT → add new thesis + journal entry
        - Options (BUY_CALL etc.)  → no thesis/journal write yet; options
          live in their own position list and don't have the thesis shape.
        """
        fill_price = alpaca_order.get("filled_avg_price", 0)
        logger.info(
            "ORDER FILLED: %s %d %s @ $%.2f",
            order.action, order.qty, order.ticker, fill_price or 0,
        )

        if order.action == "PYRAMID":
            self._apply_pyramid_memory_updates(order, fill_price)
        elif order.action in self.NEW_POSITION_ACTIONS:
            self._apply_new_position_memory_updates(order, fill_price)

        self._pending.remove(order.order_id)
        summary["orders_filled"].append({
            "ticker": order.ticker,
            "action": order.action,
            "qty": order.qty,
            "fill_price": fill_price,
        })

    def _apply_new_position_memory_updates(
        self, order: PendingOrder, fill_price: float,
    ) -> None:
        """Write thesis + journal entry after a new position's fill confirms.

        Uses Claude's decision metadata carried on the PendingOrder. If the
        metadata is empty (e.g. a PendingOrder written by an older bot
        version before this pipeline existed), we skip silently rather than
        write a degraded thesis — the user can backfill manually.
        """
        if not order.thesis:
            logger.warning(
                "FILL %s has no thesis metadata on pending order — memory not "
                "updated. Likely a pre-upgrade order; backfill manually if needed.",
                order.ticker,
            )
            return

        added = self._tm.add_thesis(
            ticker=order.ticker,
            direction=order.direction or "LONG",
            thesis=order.thesis,
            entry_price=float(fill_price or 0),
            target_price=order.target_price,
            stop_price=order.stop_price,
            timeframe=order.horizon,
            confidence=order.confidence or "medium",
        )
        if added:
            logger.info(
                "Thesis written for %s after fill (%s, target=%.2f, stop=%.2f)",
                order.ticker, order.confidence or "medium",
                order.target_price, order.stop_price,
            )
        else:
            logger.warning(
                "Fill confirmed for %s but add_thesis returned False "
                "(at max capacity or already present)", order.ticker,
            )

        # Journal entry uses Claude's short-form reasoning if present,
        # otherwise falls back to the first sentence of the thesis.
        journal_reasoning = order.decision_reasoning or (
            order.thesis.split(".")[0] if order.thesis else ""
        )
        # Journal entries historically use integer percents ("12%", not
        # "12.0%") — match the style so the file renders uniformly.
        alloc_for_journal = (
            int(round(order.allocation_pct))
            if order.allocation_pct else None
        )
        try:
            self._tm.append_journal_entry(
                date.today().isoformat(),
                [{
                    "ticker": order.ticker,
                    # Journal action label stays simple (BUY/SHORT) — strip
                    # the (CORE)/(SCOUT) tier annotation that only matters
                    # for execution routing.
                    "action": "SHORT" if order.action == "SHORT" else "BUY",
                    "allocation_pct": alloc_for_journal,
                    "reasoning": journal_reasoning,
                }],
            )
        except Exception as e:
            logger.warning("Failed to journal new position for %s: %s", order.ticker, e)

    def _apply_pyramid_memory_updates(
        self, order: PendingOrder, fill_price: float,
    ) -> None:
        """Write the deferred pyramid memory updates after a confirmed fill."""
        reasoning = order.pyramid_reasoning or "Adding to position"
        new_alloc = order.pyramid_new_alloc_pct or 0

        if self._tm.append_pyramid_note(order.ticker, reasoning, new_alloc):
            logger.info(
                "PYRAMID note appended to %s thesis (target %.0f%% alloc)",
                order.ticker, new_alloc,
            )
        else:
            logger.warning(
                "PYRAMID fill confirmed for %s but no thesis found to annotate",
                order.ticker,
            )

        try:
            self._tm.append_journal_entry(
                date.today().isoformat(),
                [{
                    "ticker": order.ticker,
                    "action": "PYRAMID",
                    "allocation_pct": new_alloc,
                    "reasoning": reasoning,
                }],
            )
        except Exception as e:
            logger.warning("Failed to journal pyramid fill for %s: %s", order.ticker, e)

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
                # Live no longer writes the position ledger — Alpaca is the
                # source of truth. We only adjust the narrative.
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
        """Detect drift between Alpaca positions and active theses.

        After the ledger refactor, Alpaca is the sole source of truth for
        position numbers in live — this method no longer writes the
        portfolio_ledger.md file. It still surfaces useful drift signals:
          - positions_added: ticker exists in Alpaca but no thesis exists yet
            (e.g., manual trade, or a fill that landed before its thesis was
            written)
          - positions_removed: thesis exists for a ticker we no longer hold
            (orphan thesis — should be moved to watching or cleared)
        Pending orders are excluded from "removed" since they're still
        on the way to becoming positions.
        """
        try:
            positions = self._market.get_positions()
        except Exception as e:
            logger.error("Failed to fetch Alpaca positions for state sync: %s", e)
            return

        alpaca_tickers = {p["ticker"] for p in positions}
        thesis_tickers = {t["ticker"] for t in self._tm.get_all_theses()}

        added = alpaca_tickers - thesis_tickers
        pending_tickers = {o.ticker for o in self._pending.get_all()}
        removed = thesis_tickers - alpaca_tickers - pending_tickers

        for ticker in added:
            logger.info("STATE SYNC: %s held in Alpaca but no active thesis exists", ticker)
            summary["positions_added"].append(ticker)
        for ticker in removed:
            logger.info("STATE SYNC: %s has an active thesis but is not held in Alpaca", ticker)
            summary["positions_removed"].append(ticker)

        summary["ledger_synced"] = True
