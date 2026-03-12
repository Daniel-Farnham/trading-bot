from __future__ import annotations

import sqlite3
from pathlib import Path

from src.storage.models import (
    SentimentRecord,
    Trade,
    TradeSide,
    TradeStatus,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    sentiment_score REAL NOT NULL,
    confidence REAL NOT NULL,
    reasoning TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    pnl REAL,
    opened_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS sentiment_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    headline TEXT NOT NULL,
    source TEXT NOT NULL,
    score REAL NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_params (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT 'system'
);
"""


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    # --- Trades ---

    def insert_trade(self, trade: Trade) -> None:
        self.conn.execute(
            """INSERT INTO trades
            (id, ticker, side, quantity, entry_price, exit_price,
             stop_loss, take_profit, sentiment_score, confidence,
             reasoning, status, pnl, opened_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            trade.to_row(),
        )
        self.conn.commit()

    def get_open_trades(self) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM trades WHERE status = 'open'"
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_trades_since(self, since_iso: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM trades WHERE opened_at >= ? ORDER BY opened_at DESC",
            (since_iso,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def close_trade(
        self, trade_id: str, exit_price: float, status: TradeStatus, pnl: float, closed_at: str
    ) -> None:
        self.conn.execute(
            """UPDATE trades
            SET exit_price = ?, status = ?, pnl = ?, closed_at = ?
            WHERE id = ?""",
            (exit_price, status.value, pnl, closed_at, trade_id),
        )
        self.conn.commit()

    def get_trade_by_id(self, trade_id: str) -> dict | None:
        cursor = self.conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_trades_by_ticker(self, ticker: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM trades WHERE ticker = ? ORDER BY opened_at DESC",
            (ticker,),
        )
        return [dict(row) for row in cursor.fetchall()]

    # --- Sentiment ---

    def insert_sentiment(self, record: SentimentRecord) -> None:
        self.conn.execute(
            """INSERT INTO sentiment_log (ticker, headline, source, score, timestamp)
            VALUES (?, ?, ?, ?, ?)""",
            record.to_row(),
        )
        self.conn.commit()

    def get_sentiment_since(self, ticker: str, since_iso: str) -> list[dict]:
        cursor = self.conn.execute(
            """SELECT * FROM sentiment_log
            WHERE ticker = ? AND timestamp >= ?
            ORDER BY timestamp DESC""",
            (ticker, since_iso),
        )
        return [dict(row) for row in cursor.fetchall()]

    # --- Strategy Params ---

    def get_param(self, key: str) -> float | None:
        cursor = self.conn.execute(
            "SELECT value FROM strategy_params WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row["value"] if row else None

    def set_param(self, key: str, value: float, updated_by: str = "system") -> None:
        from datetime import datetime

        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """INSERT INTO strategy_params (key, value, updated_at, updated_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by""",
            (key, value, now, updated_by),
        )
        self.conn.commit()

    def get_all_params(self) -> dict[str, float]:
        cursor = self.conn.execute("SELECT key, value FROM strategy_params")
        return {row["key"]: row["value"] for row in cursor.fetchall()}

    # --- Stats ---

    def get_trade_stats(self) -> dict:
        closed = self.conn.execute(
            "SELECT * FROM trades WHERE status IN ('closed', 'stopped_out')"
        ).fetchall()

        if not closed:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "avg_pnl": 0.0}

        trades = [dict(r) for r in closed]
        wins = [t for t in trades if (t["pnl"] or 0) > 0]
        losses = [t for t in trades if (t["pnl"] or 0) <= 0]
        total_pnl = sum(t["pnl"] or 0 for t in trades)

        return {
            "total": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(trades) if trades else 0.0,
            "avg_pnl": total_pnl / len(trades) if trades else 0.0,
            "total_pnl": total_pnl,
        }
