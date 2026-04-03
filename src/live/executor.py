"""Live trade executor — translates Claude's decisions into real Alpaca orders.

Mirrors the sim's _execute_decisions logic but uses the real broker.
Handles: closes, reduces, new positions (scout/core), pyramids, upgrades.
"""
from __future__ import annotations

import logging
import math

from src.execution.broker import Broker, OrderResult
from src.strategy.risk_v3 import RiskManagerV3, V3RiskVeto, PositionPlan
from src.strategy.thesis_manager import ThesisManager

logger = logging.getLogger(__name__)


class LiveExecutor:
    def __init__(
        self,
        broker: Broker,
        risk_manager: RiskManagerV3,
        thesis_manager: ThesisManager,
    ):
        self._broker = broker
        self._risk = risk_manager
        self._tm = thesis_manager

    def execute_decisions(
        self,
        response: dict,
        portfolio_value: float,
        cash: float,
        positions: list[dict],
    ) -> list[dict]:
        """Execute Claude's trade decisions against real Alpaca.

        Args:
            response: Claude's Call 3 JSON output.
            portfolio_value: Current portfolio value from Alpaca.
            cash: Current cash from Alpaca.
            positions: Current Alpaca positions list.

        Returns:
            List of executed trade dicts for logging/email.
        """
        executed = []
        position_tickers = [p.get("symbol", "") for p in positions]

        # 1. Close positions
        for close in response.get("close_positions", []):
            ticker = close.get("ticker", "")
            if ticker not in position_tickers:
                logger.warning("Close requested for %s but not in positions", ticker)
                continue

            result = self._broker.close_position(ticker)
            if result.success:
                reason = close.get("reason", "thesis invalidated")
                self._tm.remove_position(ticker)
                reentry_price = close.get("reentry_price")
                if reentry_price == 0:
                    reentry_price = None
                self._tm.move_to_watching(
                    ticker, exit_price=0, reason=reason,
                    reentry_price=reentry_price,
                )
                executed.append({
                    "ticker": ticker, "action": "CLOSE",
                    "quantity": "all", "details": reason,
                })
                logger.info("CLOSED %s — %s", ticker, reason[:80])
            else:
                logger.error("Failed to close %s: %s", ticker, result.error)

        # 2. Reduce positions
        for reduce in response.get("reduce_positions", []):
            ticker = reduce.get("ticker", "")
            if ticker not in position_tickers:
                continue

            pos = _find_position(positions, ticker)
            if not pos:
                continue

            current_qty = int(float(pos.get("qty", 0)))
            current_price = float(pos.get("current_price", 0))
            if current_qty <= 0 or current_price <= 0:
                continue

            new_alloc = reduce.get("new_allocation_pct", 5) / 100.0
            target_value = portfolio_value * new_alloc
            target_qty = math.floor(target_value / current_price)
            shares_to_sell = current_qty - target_qty

            if shares_to_sell > 0 and shares_to_sell < current_qty:
                result = self._broker.place_market_sell(ticker, shares_to_sell)
                if result.success:
                    reason = reduce.get("reason", "reducing allocation")
                    executed.append({
                        "ticker": ticker, "action": "REDUCE",
                        "quantity": shares_to_sell,
                        "details": f"Reduced by {shares_to_sell} shares — {reason}",
                    })
                    logger.info("REDUCED %s by %d shares — %s", ticker, shares_to_sell, reason[:80])

        # 3. New positions (and pyramids/upgrades)
        new_position_count = 0
        max_new = 3  # Max new positions per review

        for new_pos in response.get("new_positions", []):
            ticker = new_pos.get("ticker", "")
            if not ticker:
                continue

            action = new_pos.get("action", "BUY").upper()

            # Skip options for now (Phase 9)
            if action in ("BUY_CALL", "BUY_PUT", "SELL_PUT"):
                logger.info("OPTIONS SKIPPED %s %s — Phase 9 not yet implemented", action, ticker)
                continue

            # Pyramid/upgrade on existing position
            if ticker in position_tickers:
                trade = self._handle_pyramid_upgrade(
                    ticker, new_pos, positions, portfolio_value, cash,
                )
                if trade:
                    executed.append(trade)
                continue

            # New position — enforce cap
            if new_position_count >= max_new:
                logger.info("CAPPED %s: max %d new positions per review", ticker, max_new)
                continue
            new_position_count += 1

            trade = self._handle_new_position(
                new_pos, portfolio_value, cash, position_tickers,
            )
            if trade:
                executed.append(trade)
                position_tickers.append(ticker)

        return executed

    def _handle_pyramid_upgrade(
        self,
        ticker: str,
        new_pos: dict,
        positions: list[dict],
        portfolio_value: float,
        cash: float,
    ) -> dict | None:
        """Handle pyramid (add shares) or scout→core upgrade."""
        pos = _find_position(positions, ticker)
        if not pos:
            return None

        current_qty = int(float(pos.get("qty", 0)))
        current_price = float(pos.get("current_price", 0))
        if current_price <= 0:
            return None

        confidence = new_pos.get("confidence", "medium")
        is_core = self._risk.is_core_position(confidence)

        # Log upgrade
        if is_core:
            logger.info("UPGRADED %s → CORE (%s confidence)", ticker, confidence)

        # Pyramid: calculate additional shares needed
        target_alloc = new_pos.get("allocation_pct", 0) / 100.0
        current_value = current_qty * current_price
        current_alloc = current_value / portfolio_value if portfolio_value > 0 else 0
        additional_alloc = target_alloc - current_alloc

        if additional_alloc <= 0.02:
            return None  # Not enough to pyramid

        additional_value = portfolio_value * additional_alloc
        min_cash = portfolio_value * 0.05  # Keep 5% cash buffer
        available = cash - min_cash
        additional_value = min(additional_value, max(0, available))

        add_qty = math.floor(additional_value / current_price)
        if add_qty <= 0:
            return None

        result = self._broker.place_market_buy(ticker, add_qty)
        if result.success:
            logger.info(
                "PYRAMIDED %s: +%d shares @ ~$%.2f (target %.0f%% alloc)",
                ticker, add_qty, current_price, target_alloc * 100,
            )
            return {
                "ticker": ticker, "action": "PYRAMID",
                "quantity": add_qty,
                "details": f"+{add_qty} shares to {target_alloc*100:.0f}% allocation",
            }
        else:
            logger.error("Pyramid buy failed for %s: %s", ticker, result.error)
            return None

    def _handle_new_position(
        self,
        new_pos: dict,
        portfolio_value: float,
        cash: float,
        existing_tickers: list[str],
    ) -> dict | None:
        """Handle a new position entry (scout or core)."""
        ticker = new_pos.get("ticker", "")
        direction = new_pos.get("direction", "LONG").upper()
        confidence = new_pos.get("confidence", "medium")
        is_core = self._risk.is_core_position(confidence)
        is_short = direction == "SHORT"

        # Risk evaluation
        plan = self._risk.evaluate_new_position(
            ticker=ticker,
            side=direction,
            allocation_pct=new_pos.get("allocation_pct", 6),
            price=0,  # Will use market price via Alpaca
            portfolio_value=portfolio_value,
            cash=cash,
            open_position_count=len(existing_tickers),
            existing_tickers=existing_tickers,
            short_exposure=0,  # TODO: calculate from positions
            thesis=new_pos.get("thesis", ""),
            confidence=confidence,
        )

        if isinstance(plan, V3RiskVeto):
            logger.info("VETOED %s: %s", ticker, plan.reason)
            return None

        # Execute the order
        if is_short:
            result = self._broker.place_short_sell(ticker, plan.quantity)
            action_label = "SHORT"
        elif is_core:
            # Core: market buy, no bracket
            result = self._broker.place_market_buy(ticker, plan.quantity)
            action_label = "BUY (CORE)"
        else:
            # Scout: bracket order with mechanical stops
            stop_price = new_pos.get("stop_price", plan.catastrophic_stop)
            target_price = new_pos.get("target_price", plan.entry_price * 2)

            bracket_plan = PositionPlan(
                ticker=ticker,
                quantity=plan.quantity,
                entry_price=plan.entry_price,
                stop_loss=float(stop_price) if stop_price else plan.catastrophic_stop,
                take_profit=float(target_price) if target_price else plan.entry_price * 2,
                risk_amount=0,
                position_value=plan.position_value,
                risk_pct=0,
                is_short=is_short,
            )
            result = self._broker.place_bracket_order(bracket_plan)
            action_label = "BUY (SCOUT)"

        if result.success:
            tier = "CORE" if is_core else "SCOUT"
            logger.info(
                "%s %d %s @ market (%s, %s confidence, %.0f%% alloc)",
                action_label, plan.quantity, ticker, tier, confidence,
                plan.allocation_pct,
            )
            # Update thesis memory
            self._tm.update_thesis(ticker, entry_price=plan.entry_price)
            return {
                "ticker": ticker,
                "action": action_label,
                "quantity": plan.quantity,
                "details": (
                    f"{tier} {confidence} — {plan.allocation_pct:.0f}% alloc — "
                    f"{new_pos.get('thesis', '')[:100]}"
                ),
            }
        else:
            logger.error("Order failed for %s: %s", ticker, result.error)
            return None


def _find_position(positions: list[dict], ticker: str) -> dict | None:
    """Find a position dict by ticker/symbol."""
    for p in positions:
        if p.get("symbol", "") == ticker:
            return p
    return None
