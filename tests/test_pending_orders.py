"""Tests for pending order tracker."""
from __future__ import annotations

import json
import pytest

from src.live.pending_orders import PendingOrderTracker, PendingOrder, MAX_RETRIES


@pytest.fixture
def tracker(tmp_path):
    path = tmp_path / "pending_orders.json"
    return PendingOrderTracker(path=str(path))


class TestAdd:
    def test_adds_order(self, tracker):
        tracker.add("order-1", "NVDA", "BUY (CORE)", 50, "high", "AI thesis")
        assert tracker.count == 1
        orders = tracker.get_all()
        assert orders[0].ticker == "NVDA"
        assert orders[0].order_id == "order-1"
        assert orders[0].qty == 50

    def test_multiple_orders(self, tracker):
        tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        tracker.add("order-2", "LNG", "BUY (SCOUT)", 42)
        assert tracker.count == 2


class TestPersistence:
    def test_saves_and_loads(self, tmp_path):
        path = str(tmp_path / "pending_orders.json")
        t1 = PendingOrderTracker(path=path)
        t1.add("order-1", "NVDA", "BUY (CORE)", 50)
        t1.add("order-2", "LNG", "BUY (SCOUT)", 42)

        # Reload from disk
        t2 = PendingOrderTracker(path=path)
        assert t2.count == 2
        assert t2.get_all()[0].ticker == "NVDA"

    def test_empty_file(self, tmp_path):
        path = str(tmp_path / "pending_orders.json")
        t = PendingOrderTracker(path=path)
        assert t.count == 0


class TestRemove:
    def test_remove_by_order_id(self, tracker):
        tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        removed = tracker.remove("order-1")
        assert removed is not None
        assert removed.ticker == "NVDA"
        assert tracker.count == 0

    def test_remove_nonexistent(self, tracker):
        result = tracker.remove("nonexistent")
        assert result is None

    def test_remove_by_ticker(self, tracker):
        tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        tracker.add("order-2", "NVDA", "PYRAMID", 20)
        tracker.add("order-3", "LNG", "BUY (SCOUT)", 42)
        removed = tracker.remove_by_ticker("NVDA")
        assert len(removed) == 2
        assert tracker.count == 1


class TestRetry:
    def test_record_retry(self, tracker):
        tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        tracker.record_retry("order-1", "order-1-retry")
        orders = tracker.get_all()
        assert orders[0].order_id == "order-1-retry"
        assert orders[0].retry_count == 1

    def test_can_retry_under_max(self, tracker):
        tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        order = tracker.get_all()[0]
        assert order.can_retry is True

    def test_cannot_retry_at_max(self, tracker):
        tracker.add("order-0", "NVDA", "BUY (CORE)", 50)
        for i in range(MAX_RETRIES):
            tracker.record_retry(f"order-{i}", f"order-{i+1}")
        order = tracker.get_all()[0]
        assert order.can_retry is False


class TestHasPending:
    def test_has_pending(self, tracker):
        tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        assert tracker.has_pending("NVDA") is True
        assert tracker.has_pending("LNG") is False

    def test_update_status(self, tracker):
        tracker.add("order-1", "NVDA", "BUY (CORE)", 50)
        tracker.update_status("order-1", "partially_filled")
        assert tracker.get_all()[0].last_status == "partially_filled"
