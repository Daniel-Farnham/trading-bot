from src.storage.models import (
    Signal,
    SentimentRecord,
    Trade,
    TradeSide,
    TradeStatus,
)


class TestTrade:
    def test_create_trade_defaults(self):
        trade = Trade(
            ticker="AAPL",
            side=TradeSide.BUY,
            quantity=10,
            entry_price=150.0,
            stop_loss=145.0,
            take_profit=160.0,
            sentiment_score=0.8,
            confidence=0.75,
            reasoning="Strong positive news",
        )
        assert trade.ticker == "AAPL"
        assert trade.side == TradeSide.BUY
        assert trade.status == TradeStatus.OPEN
        assert trade.exit_price is None
        assert trade.pnl is None
        assert trade.closed_at is None
        assert len(trade.id) == 12
        assert trade.opened_at is not None

    def test_trade_to_row(self):
        trade = Trade(
            ticker="MSFT",
            side=TradeSide.SELL,
            quantity=5,
            entry_price=400.0,
            stop_loss=410.0,
            take_profit=380.0,
            sentiment_score=-0.6,
            confidence=0.9,
            reasoning="Negative earnings",
            id="test123",
        )
        row = trade.to_row()
        assert row[0] == "test123"
        assert row[1] == "MSFT"
        assert row[2] == "sell"
        assert row[3] == 5
        assert row[4] == 400.0
        assert len(row) == 15

    def test_trade_unique_ids(self):
        trades = [
            Trade(
                ticker="X", side=TradeSide.BUY, quantity=1,
                entry_price=1.0, stop_loss=0.5, take_profit=1.5,
                sentiment_score=0.5, confidence=0.5, reasoning="test",
            )
            for _ in range(100)
        ]
        ids = {t.id for t in trades}
        assert len(ids) == 100


class TestSentimentRecord:
    def test_create_sentiment_record(self):
        record = SentimentRecord(
            ticker="TSLA",
            headline="Tesla reports record deliveries",
            source="alpaca",
            score=0.92,
        )
        assert record.ticker == "TSLA"
        assert record.score == 0.92
        assert record.timestamp is not None

    def test_sentiment_to_row(self):
        record = SentimentRecord(
            ticker="NVDA",
            headline="NVIDIA beats earnings",
            source="tiingo",
            score=0.85,
        )
        row = record.to_row()
        assert len(row) == 5
        assert row[0] == "NVDA"
        assert row[3] == 0.85


class TestSignal:
    def test_create_signal(self):
        signal = Signal(
            ticker="GOOGL",
            side=TradeSide.BUY,
            confidence=0.8,
            sentiment_score=0.7,
            reasoning="Positive product launch news",
            current_price=175.0,
            stop_loss=170.0,
            take_profit=185.0,
        )
        assert signal.ticker == "GOOGL"
        assert signal.side == TradeSide.BUY
        assert signal.confidence == 0.8


class TestEnums:
    def test_trade_status_values(self):
        assert TradeStatus.OPEN.value == "open"
        assert TradeStatus.CLOSED.value == "closed"
        assert TradeStatus.STOPPED_OUT.value == "stopped_out"
        assert TradeStatus.CANCELLED.value == "cancelled"

    def test_trade_side_values(self):
        assert TradeSide.BUY.value == "buy"
        assert TradeSide.SELL.value == "sell"
