"""Tests for reconciliation manager."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from datetime import date

import pytest

from src.live.pending_orders import PendingOrderTracker
from src.live.reconciler import ReconcileManager


@pytest.fixture
def broker():
    return MagicMock()


@pytest.fixture
def market_data():
    mock = MagicMock()
    mock.get_positions.return_value = []
    mock.get_position.return_value = None
    return mock


@pytest.fixture
def thesis_manager():
    mock = MagicMock()
    mock.get_holdings.return_value = []
    return mock


@pytest.fixture
def pending_tracker(tmp_path):
    return PendingOrderTracker(path=str(tmp_path / "pending.json"))


@pytest.fixture
def reconciler(broker, market_data, thesis_manager, pending_tracker):
    return ReconcileManager(
        broker=broker,
        market_data=market_data,
        thesis_manager=thesis_manager,
        pending_tracker=pending_tracker,
    )


class TestFilledOrders:
    def test_filled_order_removed_from_pending(self, reconciler, broker, pending_tracker):
        pending_tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        broker.get_order.return_value = {
            "id": "order-1", "status": "filled", "symbol": "NVDA",
            "qty": "50", "filled_qty": "50", "filled_avg_price": 155.0,
            "side": "buy", "type": "market",
        }

        summary = reconciler._reconcile_pending_orders(
            {"orders_filled": [], "orders_retried": [], "orders_failed": []}
        )

        assert pending_tracker.count == 0

    def test_filled_order_logged_in_summary(self, reconciler, broker, pending_tracker):
        pending_tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        broker.get_order.return_value = {
            "id": "order-1", "status": "filled", "symbol": "NVDA",
            "qty": "50", "filled_qty": "50", "filled_avg_price": 155.0,
            "side": "buy", "type": "market",
        }

        summary = {"orders_filled": [], "orders_retried": [], "orders_failed": []}
        reconciler._reconcile_pending_orders(summary)

        assert len(summary["orders_filled"]) == 1
        assert summary["orders_filled"][0]["ticker"] == "NVDA"


class TestNewPositionMemoryWrites:
    """On confirmed fill, the reconciler should write thesis + journal
    entry for new-position orders (BUY CORE/SCOUT, SHORT). These writes
    are deferred from submit time so that orders that cancel overnight
    don't leave phantom theses behind.
    """

    @staticmethod
    def _fill(broker, order_id="o1", fill_price=58.41):
        broker.get_order.return_value = {
            "id": order_id, "status": "filled", "symbol": "OXY",
            "qty": "212", "filled_qty": "212", "filled_avg_price": fill_price,
            "side": "buy", "type": "market",
        }

    def test_buy_core_fill_writes_thesis_and_journal(
        self, reconciler, broker, pending_tracker, thesis_manager,
    ):
        pending_tracker.add(
            order_id="o1", ticker="OXY", action="BUY (CORE)", qty=212,
            confidence="high",
            thesis="Energy security crisis escalating with Iran controlling Hormuz. OXY has 148.9% revenue growth.",
            direction="LONG", target_price=75.0, stop_price=50.0,
            horizon="6-12 months", allocation_pct=12.0,
            decision_reasoning="Iran blockade + 148.9% revenue growth + strong technicals",
        )
        self._fill(broker, "o1")

        reconciler._reconcile_pending_orders(
            {"orders_filled": [], "orders_retried": [], "orders_failed": []}
        )

        # Thesis written with full metadata + actual fill price as entry
        thesis_manager.add_thesis.assert_called_once()
        kwargs = thesis_manager.add_thesis.call_args.kwargs
        assert kwargs["ticker"] == "OXY"
        assert kwargs["direction"] == "LONG"
        assert kwargs["entry_price"] == 58.41
        assert kwargs["target_price"] == 75.0
        assert kwargs["stop_price"] == 50.0
        assert kwargs["confidence"] == "high"

        # Journal entry written with Claude's short-form reasoning + integer alloc
        thesis_manager.append_journal_entry.assert_called_once()
        entries = thesis_manager.append_journal_entry.call_args.args[1]
        assert len(entries) == 1
        assert entries[0]["ticker"] == "OXY"
        assert entries[0]["action"] == "BUY"
        assert entries[0]["allocation_pct"] == 12  # int, not 12.0
        assert "Iran blockade" in entries[0]["reasoning"]

    def test_buy_scout_fill_writes_thesis(
        self, reconciler, broker, pending_tracker, thesis_manager,
    ):
        pending_tracker.add(
            order_id="o1", ticker="OXY", action="BUY (SCOUT)", qty=50,
            confidence="medium",
            thesis="Scout entry on emerging theme",
            direction="LONG", target_price=75.0, stop_price=50.0,
            allocation_pct=5.0,
        )
        self._fill(broker, "o1")

        reconciler._reconcile_pending_orders(
            {"orders_filled": [], "orders_retried": [], "orders_failed": []}
        )

        thesis_manager.add_thesis.assert_called_once()
        thesis_manager.append_journal_entry.assert_called_once()

    def test_pre_upgrade_order_missing_thesis_is_skipped(
        self, reconciler, broker, pending_tracker, thesis_manager,
    ):
        # Simulates a PendingOrder written by an older bot version before
        # this metadata existed. Skip memory writes rather than fabricate
        # a degraded thesis.
        pending_tracker.add(
            order_id="o1", ticker="OXY", action="BUY (CORE)", qty=212,
            confidence="high", thesis_snippet="short snippet only",
            # thesis, direction, target_price, etc. all default/empty
        )
        self._fill(broker, "o1")

        reconciler._reconcile_pending_orders(
            {"orders_filled": [], "orders_retried": [], "orders_failed": []}
        )

        thesis_manager.add_thesis.assert_not_called()
        thesis_manager.append_journal_entry.assert_not_called()


class TestExpiredOrders:
    def test_expired_order_retried(self, reconciler, broker, pending_tracker):
        pending_tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        broker.get_order.return_value = {
            "id": "order-1", "status": "expired", "symbol": "NVDA",
            "qty": "50", "filled_qty": "0", "filled_avg_price": None,
            "side": "buy", "type": "market",
        }
        from src.execution.broker import OrderResult
        broker.place_market_buy.return_value = OrderResult(
            success=True, order_id="order-1-retry",
        )

        summary = {"orders_filled": [], "orders_retried": [], "orders_failed": []}
        reconciler._reconcile_pending_orders(summary)

        assert len(summary["orders_retried"]) == 1
        assert summary["orders_retried"][0]["new_order_id"] == "order-1-retry"
        # Order still tracked with new ID
        assert pending_tracker.count == 1
        assert pending_tracker.get_all()[0].order_id == "order-1-retry"

    def test_expired_order_max_retries_exhausted(self, reconciler, broker, pending_tracker, market_data):
        pending_tracker.add("order-0", "NVDA", "BUY (CORE)", 50)
        # Exhaust retries
        for i in range(3):
            pending_tracker.record_retry(f"order-{i}", f"order-{i+1}")

        broker.get_order.return_value = {
            "id": "order-3", "status": "expired", "symbol": "NVDA",
            "qty": "50", "filled_qty": "0", "filled_avg_price": None,
            "side": "buy", "type": "market",
        }

        summary = {"orders_filled": [], "orders_retried": [], "orders_failed": []}
        reconciler._reconcile_pending_orders(summary)

        assert len(summary["orders_failed"]) == 1
        assert pending_tracker.count == 0


class TestCancelledOrders:
    def test_cancelled_order_not_retried(self, reconciler, broker, pending_tracker):
        pending_tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        broker.get_order.return_value = {
            "id": "order-1", "status": "canceled", "symbol": "NVDA",
            "qty": "50", "filled_qty": "0", "filled_avg_price": None,
            "side": "buy", "type": "market",
        }

        summary = {"orders_filled": [], "orders_retried": [], "orders_failed": []}
        reconciler._reconcile_pending_orders(summary)

        # Should NOT retry — just remove
        assert len(summary["orders_failed"]) == 1
        assert summary["orders_failed"][0]["reason"] == "canceled"
        assert pending_tracker.count == 0
        broker.place_market_buy.assert_not_called()


class TestPartialFill:
    def test_partial_fill_then_expired_retries_remainder(self, reconciler, broker, pending_tracker):
        pending_tracker.add("order-1", "LHX", "BUY (CORE)", 50)
        broker.get_order.return_value = {
            "id": "order-1", "status": "expired", "symbol": "LHX",
            "qty": "50", "filled_qty": "31", "filled_avg_price": 357.0,
            "side": "buy", "type": "market",
        }
        from src.execution.broker import OrderResult
        broker.place_market_buy.return_value = OrderResult(
            success=True, order_id="order-1-retry",
        )

        summary = {"orders_filled": [], "orders_retried": [], "orders_failed": []}
        reconciler._reconcile_pending_orders(summary)

        # Should log partial fill AND retry the remaining 19 shares
        assert len(summary["orders_filled"]) == 1
        assert summary["orders_filled"][0]["qty"] == 31
        assert summary["orders_filled"][0]["partial"] is True
        assert len(summary["orders_retried"]) == 1
        broker.place_market_buy.assert_called_once_with("LHX", 19)


class TestLedgerSync:
    """Drift detection between Alpaca positions and active theses.

    After the ledger refactor, _sync_ledger_from_alpaca no longer writes
    portfolio_ledger.md — Alpaca is the sole source of truth for live
    position numbers. The method now surfaces drift between Alpaca-held
    tickers and the active-theses store.
    """

    def test_flags_alpaca_position_without_thesis(self, reconciler, market_data, thesis_manager, pending_tracker):
        market_data.get_positions.return_value = [
            {
                "ticker": "NVDA", "qty": 80, "avg_entry": 125.0,
                "current_price": 155.0, "market_value": 12400.0,
                "unrealized_pnl": 2400.0, "unrealized_pnl_pct": 0.24,
            },
        ]
        thesis_manager.get_all_theses.return_value = []  # no thesis yet

        summary = {
            "orders_filled": [], "orders_retried": [], "orders_failed": [],
            "ledger_synced": False, "positions_added": [], "positions_removed": [],
        }
        reconciler._sync_ledger_from_alpaca(summary)

        assert summary["ledger_synced"] is True
        assert "NVDA" in summary["positions_added"]
        # No more ledger rebuild.
        thesis_manager._rebuild_ledger.assert_not_called()

    def test_flags_orphan_thesis(self, reconciler, market_data, thesis_manager, pending_tracker):
        """A thesis exists but no Alpaca position for it → flagged as removed."""
        market_data.get_positions.return_value = []
        thesis_manager.get_all_theses.return_value = [
            {"ticker": "FAKE", "direction": "LONG", "thesis": "..."},
        ]

        summary = {
            "orders_filled": [], "orders_retried": [], "orders_failed": [],
            "ledger_synced": False, "positions_added": [], "positions_removed": [],
        }
        reconciler._sync_ledger_from_alpaca(summary)

        assert "FAKE" in summary["positions_removed"]

    def test_pending_orders_not_counted_as_removed(self, reconciler, market_data, thesis_manager, pending_tracker):
        """A thesis whose position is mid-fill (pending order) shouldn't be flagged."""
        pending_tracker.add("order-1", "LNG", "BUY (CORE)", 42)
        market_data.get_positions.return_value = []
        thesis_manager.get_all_theses.return_value = [
            {"ticker": "LNG", "direction": "LONG", "thesis": "..."},
        ]

        summary = {
            "orders_filled": [], "orders_retried": [], "orders_failed": [],
            "ledger_synced": False, "positions_added": [], "positions_removed": [],
        }
        reconciler._sync_ledger_from_alpaca(summary)

        # LNG has a pending order, so should NOT be flagged as removed
        assert "LNG" not in summary["positions_removed"]


class TestFullReconcile:
    def test_full_reconcile_no_pending(self, reconciler, market_data, thesis_manager):
        market_data.get_positions.return_value = []
        thesis_manager.get_holdings.return_value = []

        summary = reconciler.reconcile()

        assert summary["ledger_synced"] is True
        assert summary["orders_filled"] == []
        assert summary["orders_retried"] == []
