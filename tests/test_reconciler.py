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
    def test_syncs_ledger_from_alpaca(self, reconciler, market_data, thesis_manager, pending_tracker):
        market_data.get_positions.return_value = [
            {
                "ticker": "NVDA", "qty": 80, "avg_entry": 125.0,
                "current_price": 155.0, "market_value": 12400.0,
                "unrealized_pnl": 2400.0, "unrealized_pnl_pct": 0.24,
            },
        ]
        thesis_manager.get_holdings.return_value = []

        summary = {
            "orders_filled": [], "orders_retried": [], "orders_failed": [],
            "ledger_synced": False, "positions_added": [], "positions_removed": [],
        }
        reconciler._sync_ledger_from_alpaca(summary)

        assert summary["ledger_synced"] is True
        assert "NVDA" in summary["positions_added"]
        thesis_manager._rebuild_ledger.assert_called_once()

    def test_removes_stale_positions(self, reconciler, market_data, thesis_manager, pending_tracker):
        market_data.get_positions.return_value = []  # Nothing in Alpaca
        thesis_manager.get_holdings.return_value = [
            {"ticker": "FAKE", "side": "LONG", "qty": 100, "entry_price": 50.0,
             "current_value": 5000.0, "date_opened": "2025-01-01"},
        ]

        summary = {
            "orders_filled": [], "orders_retried": [], "orders_failed": [],
            "ledger_synced": False, "positions_added": [], "positions_removed": [],
        }
        reconciler._sync_ledger_from_alpaca(summary)

        assert "FAKE" in summary["positions_removed"]

    def test_pending_orders_not_counted_as_removed(self, reconciler, market_data, thesis_manager, pending_tracker):
        """If a ticker is in memory but not Alpaca, and has a pending order, don't mark as removed."""
        pending_tracker.add("order-1", "LNG", "BUY (CORE)", 42)
        market_data.get_positions.return_value = []
        thesis_manager.get_holdings.return_value = [
            {"ticker": "LNG", "side": "LONG", "qty": 42, "entry_price": 200.0,
             "current_value": 8400.0, "date_opened": "2025-04-06"},
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
