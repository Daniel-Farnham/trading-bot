"""Live trade executor — translates Claude's decisions into real Alpaca orders.

Mirrors the sim's _execute_decisions logic but uses the real broker.
Handles: closes, reduces, new positions (scout/core), pyramids, upgrades, options.
"""
from __future__ import annotations

import logging
import math

from src.data.market import MarketData
from src.execution.broker import Broker, OrderResult
from src.execution.options_broker import OptionsBroker
from src.strategy.contract_selector import ContractSelector
from src.strategy.risk_v3 import RiskManagerV3, V3RiskVeto, PositionPlan
from src.strategy.thesis_manager import ThesisManager

logger = logging.getLogger(__name__)


class LiveExecutor:
    def __init__(
        self,
        broker: Broker,
        risk_manager: RiskManagerV3,
        thesis_manager: ThesisManager,
        market_data: MarketData | None = None,
        options_broker: OptionsBroker | None = None,
        contract_selector: ContractSelector | None = None,
    ):
        self._broker = broker
        self._risk = risk_manager
        self._tm = thesis_manager
        self._market = market_data
        self._options_broker = options_broker
        self._contract_selector = contract_selector

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

        # Fetch pending orders so we don't duplicate them (e.g. OPG orders over weekend)
        pending_order_tickers = set()
        try:
            pending_orders = self._broker.get_all_orders(status="open")
            pending_order_tickers = {o.get("symbol", "") for o in pending_orders}
            if pending_order_tickers:
                logger.info("Pending orders found for: %s", pending_order_tickers)
        except Exception as e:
            logger.warning("Failed to fetch pending orders: %s", e)

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
                    "order_id": result.order_id,
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
                        "order_id": result.order_id,
                    })
                    logger.info("REDUCED %s by %d shares — %s", ticker, shares_to_sell, reason[:80])

        # 3. Pyramid positions (explicit — adds shares without overwriting thesis)
        for pyr in response.get("pyramid_positions", []):
            ticker = pyr.get("ticker", "")
            if not ticker or ticker not in position_tickers:
                continue
            # Reformat as a new_pos-like dict for _handle_pyramid_upgrade
            pyr_as_pos = {
                "ticker": ticker,
                "allocation_pct": pyr.get("new_allocation_pct", 0),
                "confidence": "high",  # pyramids are conviction moves
            }
            trade = self._handle_pyramid_upgrade(
                ticker, pyr_as_pos, positions, portfolio_value, cash,
            )
            if trade:
                executed.append(trade)

        # 4. New positions (and legacy pyramids/upgrades via new_positions)
        new_position_count = 0
        max_new = 3  # Max new positions per review

        for new_pos in response.get("new_positions", []):
            ticker = new_pos.get("ticker", "")
            if not ticker:
                continue

            # Skip if there's already a pending order for this ticker
            if ticker in pending_order_tickers:
                logger.info("SKIPPED %s — already has pending order", ticker)
                continue

            action = new_pos.get("action", "BUY").upper()

            # Options — route to options broker
            if action in ("BUY_CALL", "BUY_PUT", "SELL_PUT"):
                if not self._options_broker or not self._contract_selector:
                    logger.info("OPTIONS SKIPPED %s %s — options broker not configured", action, ticker)
                    continue
                trade = self._handle_option_trade(new_pos, portfolio_value)
                if trade:
                    executed.append(trade)
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
                "order_id": result.order_id,
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

        # Fetch current price from Alpaca
        price = self._get_latest_price(ticker)
        if price <= 0:
            logger.warning("No valid price for %s — skipping", ticker)
            return None

        # Risk evaluation
        plan = self._risk.evaluate_new_position(
            ticker=ticker,
            side=direction,
            allocation_pct=new_pos.get("allocation_pct", 6),
            price=price,
            portfolio_value=portfolio_value,
            cash=cash,
            open_position_count=len(existing_tickers),
            existing_tickers=existing_tickers,
            short_exposure=self._calculate_short_exposure(existing_tickers, portfolio_value),
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
            return {
                "ticker": ticker,
                "action": action_label,
                "quantity": plan.quantity,
                "details": (
                    f"{tier} {confidence} — {plan.allocation_pct:.0f}% alloc — "
                    f"{new_pos.get('thesis', '')[:100]}"
                ),
                "order_id": result.order_id,
                "confidence": confidence,
                "thesis_snippet": new_pos.get("thesis", "")[:200],
            }
        else:
            logger.error("Order failed for %s: %s", ticker, result.error)
            return None


    def _get_latest_price(self, ticker: str) -> float:
        """Get the latest price for a ticker from Alpaca."""
        # Try MarketData first (most reliable)
        if self._market:
            price = self._market.get_latest_price(ticker)
            if price and price > 0:
                return price

        # Try from existing position
        try:
            pos = self._broker._client.get_open_position(ticker)
            return float(pos.current_price)
        except Exception:
            pass

        return 0.0

    def _calculate_short_exposure(self, position_tickers: list[str], portfolio_value: float) -> float:
        """Calculate total short exposure from current positions."""
        if portfolio_value <= 0:
            return 0.0
        try:
            positions = self._broker._client.get_all_positions()
            short_value = sum(
                abs(float(p.market_value))
                for p in positions
                if hasattr(p, 'side') and str(p.side) == 'short'
            )
            return short_value
        except Exception:
            return 0.0

    def _handle_option_trade(
        self,
        new_pos: dict,
        portfolio_value: float,
    ) -> dict | None:
        """Handle an options trade (BUY_CALL, BUY_PUT, SELL_PUT)."""
        ticker = new_pos.get("ticker", "")
        action = new_pos.get("action", "").upper()
        allocation_pct = new_pos.get("allocation_pct", 5)
        strike_selection = new_pos.get("strike_selection", "ATM")
        expiry_months = new_pos.get("expiry_months", 6)

        # Check options premium cap (max 25% of portfolio)
        # Options premium cap check (max 25% of portfolio)
        if self._options_broker:
            options_positions = self._options_broker.get_options_positions()
            options_value = sum(abs(float(p.get("market_value", 0))) for p in options_positions)
            options_pct = options_value / portfolio_value if portfolio_value > 0 else 0
            if options_pct > 0.25:
                logger.info("OPTIONS CAP: %.1f%% of portfolio in options (max 25%%), skipping", options_pct * 100)
                return None

        allocation_usd = portfolio_value * (allocation_pct / 100.0)

        # Get current underlying price
        current_price = self._get_latest_price(ticker)
        if current_price <= 0:
            logger.warning("No price for %s, cannot select options contract", ticker)
            return None

        # Select best contract from real chain
        selected = self._contract_selector.select_contract(
            ticker=ticker,
            action=action,
            current_price=current_price,
            allocation_usd=allocation_usd,
            strike_selection=strike_selection,
            expiry_months=expiry_months,
        )

        if not selected:
            logger.warning("No suitable contract found for %s %s", action, ticker)
            return None

        # Execute via options broker
        if action == "BUY_CALL":
            result = self._options_broker.buy_to_open(selected.symbol, selected.quantity)
        elif action == "BUY_PUT":
            result = self._options_broker.buy_to_open(selected.symbol, selected.quantity)
        elif action == "SELL_PUT":
            result = self._options_broker.sell_to_open(selected.symbol, selected.quantity)
        else:
            return None

        if result.success:
            logger.info(
                "OPTION %s: %d x %s $%.0f %s exp %s @ $%.2f ($%.0f total)",
                action, selected.quantity, ticker, selected.strike,
                selected.option_type, selected.expiry, selected.premium,
                selected.total_cost,
            )
            return {
                "ticker": ticker,
                "action": action,
                "quantity": selected.quantity,
                "details": (
                    f"{selected.option_type.upper()} ${selected.strike:.0f} "
                    f"exp {selected.expiry} @ ${selected.premium:.2f} "
                    f"({selected.quantity} contracts, ${selected.total_cost:.0f} total, "
                    f"delta {selected.delta:.2f})"
                ),
                "order_id": result.order_id,
            }
        else:
            logger.error("Options order failed for %s: %s", ticker, result.error)
            return None


def _find_position(positions: list[dict], ticker: str) -> dict | None:
    """Find a position dict by ticker/symbol."""
    for p in positions:
        if p.get("symbol", "") == ticker:
            return p
    return None
