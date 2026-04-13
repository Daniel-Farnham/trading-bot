"""Tracks submitted orders awaiting fill confirmation from Alpaca.

Persists to JSON so the 30-min trigger check can reconcile order status
and retry expired/cancelled orders.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = "data/live/pending_orders.json"
MAX_RETRIES = 3


@dataclass
class PendingOrder:
    order_id: str
    ticker: str
    action: str  # BUY, SHORT, PYRAMID, BUY_CALL, etc.
    qty: int
    confidence: str  # from Claude's decision
    thesis_snippet: str  # short thesis for context
    submitted_at: str  # ISO timestamp
    retry_count: int = 0
    last_status: str = "new"  # new, partially_filled, expired, cancelled, filled, failed
    # Pyramid-only metadata: written to MU's thesis as a [PYRAMID] note when
    # the order actually fills (not when it's merely accepted by the broker).
    # Defaults preserve backward compat with pre-existing pending_orders.json.
    pyramid_reasoning: str = ""
    pyramid_new_alloc_pct: float = 0.0

    @property
    def can_retry(self) -> bool:
        return self.retry_count < MAX_RETRIES


class PendingOrderTracker:
    def __init__(self, path: str = DEFAULT_PATH):
        self._path = Path(path)
        self._orders: list[PendingOrder] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._orders = []
            return
        try:
            raw = json.loads(self._path.read_text())
            self._orders = [PendingOrder(**o) for o in raw]
        except Exception as e:
            logger.warning("Failed to load pending orders: %s", e)
            self._orders = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(
            [asdict(o) for o in self._orders], indent=2,
        ))

    def add(
        self,
        order_id: str,
        ticker: str,
        action: str,
        qty: int,
        confidence: str = "",
        thesis_snippet: str = "",
        pyramid_reasoning: str = "",
        pyramid_new_alloc_pct: float = 0.0,
    ) -> None:
        self._orders.append(PendingOrder(
            order_id=order_id,
            ticker=ticker,
            action=action,
            qty=qty,
            confidence=confidence,
            thesis_snippet=thesis_snippet,
            submitted_at=datetime.now().isoformat(),
            pyramid_reasoning=pyramid_reasoning,
            pyramid_new_alloc_pct=pyramid_new_alloc_pct,
        ))
        self._save()
        logger.info("Tracking pending order: %s %d %s [%s]", action, qty, ticker, order_id)

    def get_all(self) -> list[PendingOrder]:
        return list(self._orders)

    def update_status(self, order_id: str, status: str) -> None:
        for o in self._orders:
            if o.order_id == order_id:
                o.last_status = status
                break
        self._save()

    def record_retry(self, old_order_id: str, new_order_id: str) -> None:
        """Replace order ID after a retry and bump retry count."""
        for o in self._orders:
            if o.order_id == old_order_id:
                o.order_id = new_order_id
                o.retry_count += 1
                o.last_status = "retried"
                o.submitted_at = datetime.now().isoformat()
                break
        self._save()

    def remove(self, order_id: str) -> PendingOrder | None:
        """Remove a tracked order (filled or permanently failed). Returns it."""
        for i, o in enumerate(self._orders):
            if o.order_id == order_id:
                removed = self._orders.pop(i)
                self._save()
                return removed
        return None

    def remove_by_ticker(self, ticker: str) -> list[PendingOrder]:
        """Remove all tracked orders for a ticker."""
        removed = [o for o in self._orders if o.ticker == ticker]
        self._orders = [o for o in self._orders if o.ticker != ticker]
        if removed:
            self._save()
        return removed

    def has_pending(self, ticker: str) -> bool:
        return any(o.ticker == ticker for o in self._orders)

    @property
    def count(self) -> int:
        return len(self._orders)
