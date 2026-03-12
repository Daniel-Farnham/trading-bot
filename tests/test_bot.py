from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.bot import TradingBot
from src.data.news import NewsArticle
from src.execution.broker import OrderResult
from src.storage.database import Database
from src.storage.models import SentimentRecord, TradeSide
from src.strategy.themes import ThemeManager


def _make_articles(ticker: str = "AAPL", count: int = 3) -> list[NewsArticle]:
    return [
        NewsArticle(
            headline=f"Great news for {ticker} #{i}",
            summary="Positive stuff.",
            source="test",
            ticker=ticker,
            url="https://example.com",
            published_at="2025-06-01T10:00:00Z",
        )
        for i in range(count)
    ]


def _make_bars(num: int = 60) -> pd.DataFrame:
    import numpy as np
    np.random.seed(42)
    closes = [150.0]
    for _ in range(1, num):
        closes.append(closes[-1] + np.random.randn() * 2)
    closes = pd.Series(closes)
    return pd.DataFrame({
        "open": closes + 0.5,
        "high": closes + 2,
        "low": closes - 2,
        "close": closes,
        "volume": [1000000] * num,
    })


@pytest.fixture
def bot_fixture():
    """Creates a TradingBot with all dependencies mocked."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.connect()

        market = MagicMock()
        market.is_market_open.return_value = True
        market.get_account.return_value = {
            "equity": 100000.0,
            "cash": 80000.0,
            "buying_power": 160000.0,
            "portfolio_value": 100000.0,
            "currency": "USD",
        }
        market.get_positions.return_value = []
        market.get_bars.return_value = _make_bars()

        news = MagicMock()
        news.fetch_news.return_value = _make_articles()

        sentiment = MagicMock()
        sentiment.score_articles.return_value = [
            SentimentRecord("AAPL", "Great news", "test", 0.85, "2025-06-01"),
        ]
        sentiment.aggregate_sentiment.return_value = 0.85

        broker = MagicMock()
        broker.place_bracket_order.return_value = OrderResult(
            success=True, order_id="test_order_1"
        )
        broker.close_position.return_value = OrderResult(success=True)

        bot = TradingBot(
            market=market,
            news=news,
            sentiment=sentiment,
            broker=broker,
            db=db,
            themes=ThemeManager(themes=[]),
            watchlist=MagicMock(symbols=["AAPL", "MSFT"], __iter__=lambda self: iter(["AAPL", "MSFT"])),
        )

        yield bot, market, news, sentiment, broker

        db.close()


class TestTradingBotCycle:
    def test_run_cycle_places_trades(self, bot_fixture):
        bot, market, news, sentiment, broker = bot_fixture

        summary = bot.run_cycle()

        assert summary["trades_placed"] > 0
        assert summary.get("skipped") is None

    def test_skips_when_market_closed(self, bot_fixture):
        bot, market, *_ = bot_fixture
        market.is_market_open.return_value = False

        summary = bot.run_cycle()

        assert summary["skipped"] == "market_closed"
        assert summary["trades_placed"] == 0

    def test_skips_on_max_drawdown(self, bot_fixture):
        bot, market, *_ = bot_fixture
        bot._peak_value = 200000.0  # Portfolio dropped from 200k to 100k = 50% drawdown
        market.get_account.return_value["portfolio_value"] = 100000.0

        summary = bot.run_cycle()

        assert summary["skipped"] == "max_drawdown"

    def test_logs_sentiment_to_db(self, bot_fixture):
        bot, *_ = bot_fixture

        bot.run_cycle()

        records = bot.db.get_sentiment_since("AAPL", "2025-01-01")
        assert len(records) > 0

    def test_logs_trade_to_db(self, bot_fixture):
        bot, *_ = bot_fixture

        bot.run_cycle()

        open_trades = bot.db.get_open_trades()
        assert len(open_trades) > 0

    def test_handles_news_fetch_error(self, bot_fixture):
        bot, market, news, *_ = bot_fixture
        news.fetch_news.side_effect = Exception("API error")

        summary = bot.run_cycle()

        # Should log errors but not crash
        assert len(summary["errors"]) > 0

    def test_handles_account_fetch_error(self, bot_fixture):
        bot, market, *_ = bot_fixture
        market.get_account.side_effect = Exception("Connection error")

        summary = bot.run_cycle()

        assert len(summary["errors"]) > 0

    def test_no_trades_on_low_sentiment(self, bot_fixture):
        bot, market, news, sentiment, broker = bot_fixture
        sentiment.aggregate_sentiment.return_value = 0.2  # Below threshold

        summary = bot.run_cycle()

        assert summary["trades_placed"] == 0

    def test_vetoes_when_max_positions(self, bot_fixture):
        bot, market, news, sentiment, broker = bot_fixture
        # Simulate 10 open positions
        market.get_positions.return_value = [
            {"ticker": f"STOCK{i}", "qty": 10, "avg_entry": 100.0,
             "current_price": 105.0, "market_value": 1050.0,
             "unrealized_pnl": 50.0, "unrealized_pnl_pct": 0.05}
            for i in range(10)
        ]

        summary = bot.run_cycle()

        assert summary["trades_vetoed"] > 0


class TestTradingBotExits:
    def test_exits_on_negative_sentiment(self, bot_fixture):
        bot, market, news, sentiment, broker = bot_fixture

        # Set up existing position
        market.get_positions.return_value = [
            {"ticker": "AAPL", "qty": 10, "avg_entry": 150.0,
             "current_price": 145.0, "market_value": 1450.0,
             "unrealized_pnl": -50.0, "unrealized_pnl_pct": -0.033}
        ]

        # For the exit check pass, return negative sentiment
        # First call is for the scan loop, second is for exit check
        sentiment.aggregate_sentiment.side_effect = [0.85, 0.85, -0.6]

        bot.run_cycle()

        broker.close_position.assert_called()


class TestTradingBotStatus:
    def test_get_status(self, bot_fixture):
        bot, *_ = bot_fixture

        status = bot.get_status()

        assert "account" in status
        assert "positions" in status
        assert "stats" in status
        assert "themes" in status

    def test_get_status_handles_error(self, bot_fixture):
        bot, market, *_ = bot_fixture
        market.get_account.side_effect = Exception("Failed")

        status = bot.get_status()
        assert "error" in status
