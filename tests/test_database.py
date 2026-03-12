import pytest

from src.storage.database import Database
from src.storage.models import (
    SentimentRecord,
    Trade,
    TradeSide,
    TradeStatus,
)


def _make_trade(**overrides) -> Trade:
    defaults = {
        "ticker": "AAPL",
        "side": TradeSide.BUY,
        "quantity": 10,
        "entry_price": 150.0,
        "stop_loss": 145.0,
        "take_profit": 160.0,
        "sentiment_score": 0.8,
        "confidence": 0.75,
        "reasoning": "Test trade",
    }
    defaults.update(overrides)
    return Trade(**defaults)


class TestDatabaseConnection:
    def test_connect_creates_tables(self, tmp_db: Database):
        cursor = tmp_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row["name"] for row in cursor.fetchall()}
        assert "trades" in tables
        assert "sentiment_log" in tables
        assert "strategy_params" in tables

    def test_connect_is_idempotent(self, tmp_db: Database):
        # Calling connect again should not fail or drop data
        tmp_db.insert_trade(_make_trade())
        tmp_db.connect()
        assert len(tmp_db.get_open_trades()) == 1


class TestTrades:
    def test_insert_and_retrieve_trade(self, tmp_db: Database):
        trade = _make_trade(id="abc123")
        tmp_db.insert_trade(trade)

        result = tmp_db.get_trade_by_id("abc123")
        assert result is not None
        assert result["ticker"] == "AAPL"
        assert result["side"] == "buy"
        assert result["quantity"] == 10
        assert result["entry_price"] == 150.0
        assert result["status"] == "open"

    def test_get_open_trades(self, tmp_db: Database):
        tmp_db.insert_trade(_make_trade(id="t1", ticker="AAPL"))
        tmp_db.insert_trade(_make_trade(id="t2", ticker="MSFT"))

        open_trades = tmp_db.get_open_trades()
        assert len(open_trades) == 2

    def test_close_trade(self, tmp_db: Database):
        trade = _make_trade(id="close_me")
        tmp_db.insert_trade(trade)

        tmp_db.close_trade(
            trade_id="close_me",
            exit_price=155.0,
            status=TradeStatus.CLOSED,
            pnl=50.0,
            closed_at="2025-06-01T16:00:00",
        )

        result = tmp_db.get_trade_by_id("close_me")
        assert result["status"] == "closed"
        assert result["exit_price"] == 155.0
        assert result["pnl"] == 50.0
        assert result["closed_at"] == "2025-06-01T16:00:00"

    def test_closed_trade_not_in_open(self, tmp_db: Database):
        tmp_db.insert_trade(_make_trade(id="t1"))
        tmp_db.close_trade("t1", 155.0, TradeStatus.CLOSED, 50.0, "2025-06-01")

        assert len(tmp_db.get_open_trades()) == 0

    def test_get_trade_by_id_not_found(self, tmp_db: Database):
        result = tmp_db.get_trade_by_id("nonexistent")
        assert result is None

    def test_get_trades_by_ticker(self, tmp_db: Database):
        tmp_db.insert_trade(_make_trade(id="a1", ticker="AAPL"))
        tmp_db.insert_trade(_make_trade(id="a2", ticker="AAPL"))
        tmp_db.insert_trade(_make_trade(id="m1", ticker="MSFT"))

        aapl_trades = tmp_db.get_trades_by_ticker("AAPL")
        assert len(aapl_trades) == 2

        msft_trades = tmp_db.get_trades_by_ticker("MSFT")
        assert len(msft_trades) == 1

    def test_get_trades_since(self, tmp_db: Database):
        tmp_db.insert_trade(
            _make_trade(id="old", opened_at="2025-01-01T00:00:00")
        )
        tmp_db.insert_trade(
            _make_trade(id="new", opened_at="2025-06-01T00:00:00")
        )

        recent = tmp_db.get_trades_since("2025-03-01T00:00:00")
        assert len(recent) == 1
        assert recent[0]["id"] == "new"

    def test_duplicate_trade_id_raises(self, tmp_db: Database):
        tmp_db.insert_trade(_make_trade(id="dup"))
        with pytest.raises(Exception):
            tmp_db.insert_trade(_make_trade(id="dup"))


class TestSentiment:
    def test_insert_and_retrieve_sentiment(self, tmp_db: Database):
        record = SentimentRecord(
            ticker="AAPL",
            headline="Apple launches new iPhone",
            source="alpaca",
            score=0.85,
            timestamp="2025-06-01T10:00:00",
        )
        tmp_db.insert_sentiment(record)

        results = tmp_db.get_sentiment_since("AAPL", "2025-06-01T00:00:00")
        assert len(results) == 1
        assert results[0]["headline"] == "Apple launches new iPhone"
        assert results[0]["score"] == 0.85

    def test_sentiment_filters_by_ticker(self, tmp_db: Database):
        tmp_db.insert_sentiment(
            SentimentRecord("AAPL", "Apple news", "test", 0.9, "2025-06-01T10:00:00")
        )
        tmp_db.insert_sentiment(
            SentimentRecord("MSFT", "Microsoft news", "test", 0.7, "2025-06-01T10:00:00")
        )

        results = tmp_db.get_sentiment_since("AAPL", "2025-01-01T00:00:00")
        assert len(results) == 1
        assert results[0]["ticker"] == "AAPL"

    def test_sentiment_filters_by_date(self, tmp_db: Database):
        tmp_db.insert_sentiment(
            SentimentRecord("AAPL", "Old news", "test", 0.5, "2025-01-01T10:00:00")
        )
        tmp_db.insert_sentiment(
            SentimentRecord("AAPL", "New news", "test", 0.9, "2025-06-01T10:00:00")
        )

        results = tmp_db.get_sentiment_since("AAPL", "2025-03-01T00:00:00")
        assert len(results) == 1
        assert results[0]["headline"] == "New news"


class TestStrategyParams:
    def test_set_and_get_param(self, tmp_db: Database):
        tmp_db.set_param("sentiment_buy_threshold", 0.6)
        assert tmp_db.get_param("sentiment_buy_threshold") == 0.6

    def test_update_existing_param(self, tmp_db: Database):
        tmp_db.set_param("rsi_overbought", 70.0)
        tmp_db.set_param("rsi_overbought", 75.0)
        assert tmp_db.get_param("rsi_overbought") == 75.0

    def test_get_nonexistent_param(self, tmp_db: Database):
        assert tmp_db.get_param("does_not_exist") is None

    def test_get_all_params(self, tmp_db: Database):
        tmp_db.set_param("a", 1.0)
        tmp_db.set_param("b", 2.0)
        tmp_db.set_param("c", 3.0)

        params = tmp_db.get_all_params()
        assert params == {"a": 1.0, "b": 2.0, "c": 3.0}

    def test_set_param_with_updated_by(self, tmp_db: Database):
        tmp_db.set_param("threshold", 0.5, updated_by="claude_review")
        cursor = tmp_db.conn.execute(
            "SELECT updated_by FROM strategy_params WHERE key = 'threshold'"
        )
        assert cursor.fetchone()["updated_by"] == "claude_review"


class TestTradeStats:
    def test_stats_empty(self, tmp_db: Database):
        stats = tmp_db.get_trade_stats()
        assert stats["total"] == 0
        assert stats["win_rate"] == 0.0

    def test_stats_with_trades(self, tmp_db: Database):
        # Two winning trades
        t1 = _make_trade(id="w1")
        tmp_db.insert_trade(t1)
        tmp_db.close_trade("w1", 160.0, TradeStatus.CLOSED, 100.0, "2025-06-01")

        t2 = _make_trade(id="w2")
        tmp_db.insert_trade(t2)
        tmp_db.close_trade("w2", 155.0, TradeStatus.CLOSED, 50.0, "2025-06-01")

        # One losing trade
        t3 = _make_trade(id="l1")
        tmp_db.insert_trade(t3)
        tmp_db.close_trade("l1", 140.0, TradeStatus.STOPPED_OUT, -100.0, "2025-06-01")

        stats = tmp_db.get_trade_stats()
        assert stats["total"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert abs(stats["win_rate"] - 2 / 3) < 0.01
        assert stats["total_pnl"] == 50.0

    def test_stats_ignores_open_trades(self, tmp_db: Database):
        tmp_db.insert_trade(_make_trade(id="open1"))
        stats = tmp_db.get_trade_stats()
        assert stats["total"] == 0
