from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4


class TradeStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
    STOPPED_OUT = "stopped_out"
    CANCELLED = "cancelled"


class TradeSide(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Trade:
    ticker: str
    side: TradeSide
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: float
    sentiment_score: float
    confidence: float
    reasoning: str
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    exit_price: float | None = None
    status: TradeStatus = TradeStatus.OPEN
    pnl: float | None = None
    opened_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    closed_at: str | None = None

    def to_row(self) -> tuple:
        return (
            self.id,
            self.ticker,
            self.side.value,
            self.quantity,
            self.entry_price,
            self.exit_price,
            self.stop_loss,
            self.take_profit,
            self.sentiment_score,
            self.confidence,
            self.reasoning,
            self.status.value,
            self.pnl,
            self.opened_at,
            self.closed_at,
        )


@dataclass
class SentimentRecord:
    ticker: str
    headline: str
    source: str
    score: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_row(self) -> tuple:
        return (self.ticker, self.headline, self.source, self.score, self.timestamp)


@dataclass
class Signal:
    ticker: str
    side: TradeSide
    confidence: float
    sentiment_score: float
    reasoning: str
    current_price: float
    stop_loss: float
    take_profit: float
