"""Live trade executor — translates Claude's decisions into real Alpaca orders.

Mirrors the sim's _execute_decisions logic but uses the real broker.
Handles: closes, reduces, new positions (scout/core), pyramids, upgrades, options.
"""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from src.data.market import MarketData
from src.execution.broker import Broker, OrderResult
from src.execution.options_broker import OptionsBroker
from src.strategy.contract_selector import ContractSelector
from src.strategy.risk_v3 import RiskManagerV3, V3RiskVeto, PositionPlan
from src.strategy.thesis_manager import ThesisManager

if TYPE_CHECKING:
    from src.live.portfolio_state import PortfolioSnapshot

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
        snapshot: "PortfolioSnapshot | None" = None,
    ) -> list[dict]:
        """Execute Claude's trade decisions against real Alpaca.

        Order of operations is atomic by phase:
          1. Closes execute first (frees cash, frees position slots).
          2. Reduces execute next (frees cash).
          3. Cash-math validator runs against expected funds AFTER stages 1-2.
             If sum(BUY $) > available_for_new_buys + freed, ALL BUYs and
             PYRAMIDs are dropped — closes/reduces still go through so the
             next call has more cash.
          4. Pyramids + new positions execute, with a running cash counter
             decremented per iteration as defense in depth.

        Args:
            response: Claude's Call 3 JSON output.
            portfolio_value: Current portfolio value from Alpaca.
            cash: Current cash from Alpaca.
            positions: Current Alpaca positions list.
            snapshot: Live portfolio snapshot. Required for atomic cash math —
                if None, the validator is skipped (back-compat for callers
                that haven't been migrated).

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

        # 2.5 Atomic cash-math validator — runs AFTER closes/reduces have
        # been submitted, BEFORE any buys are evaluated. Compares Claude's
        # proposed buy spend against the cash that will be available given
        # the closes/reduces. If overshoots, drop all BUYs/PYRAMIDs for
        # this call; closes/reduces still stand so the next call has room.
        skip_buys = False
        if snapshot is not None:
            ok, reason = self._validate_cash_math(response, positions, portfolio_value, snapshot)
            if not ok:
                logger.warning("CASH MATH FAIL: %s — dropping new BUYs and PYRAMIDs for this call", reason)
                skip_buys = True

        # If over the position limit, only honor closes/reduces — never add
        # capacity (BUYs or PYRAMIDs) when we're already over the cap.
        if snapshot is not None and snapshot.account.over_limit > 0:
            if not skip_buys:
                logger.warning(
                    "OVER POSITION LIMIT (%d/%d) — dropping new BUYs and PYRAMIDs for this call",
                    snapshot.account.position_count, snapshot.account.max_positions,
                )
            skip_buys = True

        if skip_buys:
            return executed

        # Running cash counter for defense in depth — even though the
        # validator above checks the response as a whole, decrement per
        # iteration so risk_v3's per-trade cash-reserve check evaluates
        # against the correct remaining balance.
        running_cash = cash

        # 3. Pyramid positions (explicit — adds shares without overwriting thesis)
        for pyr in response.get("pyramid_positions", []):
            ticker = pyr.get("ticker", "")
            if not ticker:
                logger.info("PYRAMID SKIPPED: missing ticker in %s", pyr)
                continue
            if ticker not in position_tickers:
                logger.info(
                    "PYRAMID SKIPPED %s: ticker not in current positions", ticker,
                )
                continue
            # Reformat as a new_pos-like dict for _handle_pyramid_upgrade
            pyr_as_pos = {
                "ticker": ticker,
                "allocation_pct": pyr.get("new_allocation_pct", 0),
                "confidence": "high",  # pyramids are conviction moves
            }
            trade = self._handle_pyramid_upgrade(
                ticker, pyr_as_pos, positions, portfolio_value, running_cash,
            )
            if trade:
                # Carry pyramid metadata so the orchestrator can stash it on the
                # pending order — the reconciler will write the thesis note when
                # the order actually fills, not when it's merely accepted.
                trade["pyramid_reasoning"] = pyr.get("reasoning", "")
                trade["pyramid_new_alloc_pct"] = pyr.get("new_allocation_pct", 0)
                executed.append(trade)
                running_cash -= trade.get("position_value", 0)

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
                    ticker, new_pos, positions, portfolio_value, running_cash,
                )
                if trade:
                    executed.append(trade)
                    running_cash -= trade.get("position_value", 0)
                continue

            # New position — enforce cap
            if new_position_count >= max_new:
                logger.info("CAPPED %s: max %d new positions per review", ticker, max_new)
                continue
            new_position_count += 1

            trade = self._handle_new_position(
                new_pos, portfolio_value, running_cash, position_tickers,
            )
            if trade:
                executed.append(trade)
                position_tickers.append(ticker)
                running_cash -= trade.get("position_value", 0)

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
            logger.info(
                "PYRAMID SKIPPED %s: no Alpaca position found (memory may be stale)",
                ticker,
            )
            return None

        current_qty = int(float(pos.get("qty", 0)))
        current_price = float(pos.get("current_price", 0))
        if current_price <= 0:
            logger.info(
                "PYRAMID SKIPPED %s: no valid current price from Alpaca", ticker,
            )
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
            logger.info(
                "PYRAMID SKIPPED %s: target alloc %.1f%% only +%.1fpp above "
                "current %.1f%% (need >2pp delta to act)",
                ticker, target_alloc * 100, additional_alloc * 100, current_alloc * 100,
            )
            return None

        additional_value = portfolio_value * additional_alloc
        min_cash = portfolio_value * 0.05  # Keep 5% cash buffer
        available = cash - min_cash
        additional_value = min(additional_value, max(0, available))

        add_qty = math.floor(additional_value / current_price)
        if add_qty <= 0:
            logger.info(
                "PYRAMID SKIPPED %s: cash %.2f after 5%% buffer (%.2f) buys "
                "0 shares at $%.2f",
                ticker, cash, min_cash, current_price,
            )
            return None

        result = self._broker.place_market_buy(ticker, add_qty)
        if result.success and result.order_id:
            logger.info(
                "PYRAMIDED %s: +%d shares @ ~$%.2f (target %.0f%% alloc)",
                ticker, add_qty, current_price, target_alloc * 100,
            )
            return {
                "ticker": ticker, "action": "PYRAMID",
                "quantity": add_qty,
                "details": f"+{add_qty} shares to {target_alloc*100:.0f}% allocation",
                "order_id": result.order_id,
                "position_value": float(add_qty * current_price),
            }
        else:
            logger.error(
                "Pyramid buy failed for %s: %s",
                ticker, result.error or "broker returned no order_id",
            )
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

            # Safety rail: refuse to forward a bracket whose levels are inverted
            # against the live price (Alpaca rejects with code 42210000). This
            # catches stale-target hallucinations from upstream.
            valid, reason = self._validate_bracket_levels(bracket_plan, price)
            if not valid:
                logger.error(
                    "Bracket levels rejected for %s: %s "
                    "(live=%.2f, target=%.2f, stop=%.2f)",
                    ticker, reason, price,
                    bracket_plan.take_profit, bracket_plan.stop_loss,
                )
                return None

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
                "position_value": float(plan.position_value),
                # Full metadata — orchestrator forwards these to the pending
                # tracker, reconciler writes them to memory on confirmed fill.
                "thesis": new_pos.get("thesis", ""),
                "direction": direction,
                "target_price": float(new_pos.get("target_price") or 0),
                "stop_price": float(new_pos.get("stop_price") or 0),
                "horizon": new_pos.get("horizon", ""),
                "invalidation": new_pos.get("invalidation", ""),
                "allocation_pct": float(new_pos.get("allocation_pct") or 0),
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

    def _validate_cash_math(
        self,
        response: dict,
        positions: list[dict],
        portfolio_value: float,
        snapshot: "PortfolioSnapshot",
    ) -> tuple[bool, str]:
        """Atomic cash check: do Claude's BUYs fit, given his proposed CLOSEs/REDUCEs?

        Returns (True, "") if buys fit; (False, reason) otherwise.

        funds_freed  = market value of proposed closes + value released by proposed reduces
        funds_used   = sum of BUY allocations + pyramid deltas (in $)
        Must satisfy: available_for_new_buys + funds_freed - funds_used >= 0.

        Pyramid delta = (target_alloc - current_alloc) * portfolio_value, floored at 0.
        We use Alpaca-derived `market_value` and `current_price` from the
        snapshot/positions list — same numbers risk_v3 will see.
        """
        if portfolio_value <= 0:
            return False, "portfolio_value is 0 — cannot evaluate"

        held_by_ticker = {p.get("symbol", ""): p for p in positions}

        # Funds freed by closes
        funds_freed = 0.0
        for close in response.get("close_positions", []):
            ticker = close.get("ticker", "")
            pos = held_by_ticker.get(ticker)
            if pos:
                funds_freed += float(pos.get("market_value", 0))

        # Funds freed by reduces
        for reduce in response.get("reduce_positions", []):
            ticker = reduce.get("ticker", "")
            pos = held_by_ticker.get(ticker)
            if not pos:
                continue
            current_qty = int(float(pos.get("qty", 0)))
            current_price = float(pos.get("current_price", 0))
            if current_qty <= 0 or current_price <= 0:
                continue
            new_alloc = float(reduce.get("new_allocation_pct", 0)) / 100.0
            target_value = portfolio_value * new_alloc
            target_qty = math.floor(target_value / current_price)
            shares_sold = max(0, current_qty - target_qty)
            funds_freed += shares_sold * current_price

        # Funds used by buys (new positions excluding existing-ticker pyramids)
        # and by pyramid_positions
        funds_used = 0.0
        held_tickers = {p.get("symbol", "") for p in positions}

        for new_pos in response.get("new_positions", []):
            ticker = new_pos.get("ticker", "")
            action = (new_pos.get("action") or "BUY").upper()
            # Options handled separately via options broker; skip for cash math.
            if action in ("BUY_CALL", "BUY_PUT", "SELL_PUT"):
                continue
            # Shorts don't consume long cash.
            if (new_pos.get("direction") or "LONG").upper() == "SHORT":
                continue
            alloc = float(new_pos.get("allocation_pct") or 0) / 100.0
            if ticker in held_tickers:
                # Routed as an implicit pyramid by the new_positions loop —
                # delta against current allocation
                pos = held_by_ticker.get(ticker, {})
                current_value = float(pos.get("market_value", 0))
                current_alloc = current_value / portfolio_value
                delta = max(0.0, alloc - current_alloc)
                funds_used += delta * portfolio_value
            else:
                funds_used += alloc * portfolio_value

        for pyr in response.get("pyramid_positions", []):
            ticker = pyr.get("ticker", "")
            pos = held_by_ticker.get(ticker)
            if not pos:
                continue
            current_value = float(pos.get("market_value", 0))
            current_alloc = current_value / portfolio_value
            target_alloc = float(pyr.get("new_allocation_pct") or 0) / 100.0
            delta = max(0.0, target_alloc - current_alloc)
            funds_used += delta * portfolio_value

        net = snapshot.account.available_for_new_buys + funds_freed - funds_used

        if net < 0:
            return False, (
                f"buys ${funds_used:,.0f} exceed available "
                f"${snapshot.account.available_for_new_buys:,.0f} + freed "
                f"${funds_freed:,.0f} (short ${-net:,.0f})"
            )
        return True, ""

    @staticmethod
    def _validate_bracket_levels(
        plan: PositionPlan, live_price: float,
    ) -> tuple[bool, str]:
        """Check that a LONG bracket's target/stop are sane vs the live price.

        Alpaca enforces take_profit > base_price + 0.01 and stop < base_price.
        Validating here means we never burn an API call on Claude's stale
        narrative-anchored levels (e.g. $45 target on a stock at $71.75).
        Returns (True, "") if valid, else (False, reason).
        """
        if plan.is_short:
            # Shorts don't use place_bracket_order today; if that ever changes,
            # the inequality flips and this needs a SHORT branch.
            return True, ""
        if live_price <= 0:
            return False, "no live price available"
        if plan.take_profit <= live_price + 0.01:
            return False, (
                f"take_profit {plan.take_profit:.2f} not above live price "
                f"{live_price:.2f} (likely stale target — stock has moved)"
            )
        if plan.stop_loss >= live_price - 0.01:
            return False, (
                f"stop_loss {plan.stop_loss:.2f} not below live price "
                f"{live_price:.2f} (would trigger immediately)"
            )
        if plan.take_profit <= plan.stop_loss:
            return False, (
                f"take_profit {plan.take_profit:.2f} <= stop_loss "
                f"{plan.stop_loss:.2f} (inverted)"
            )
        return True, ""

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
