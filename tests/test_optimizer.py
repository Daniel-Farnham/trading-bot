from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.adaptation.optimizer import StrategyOptimizer
from src.storage.database import Database
from src.storage.models import Trade, TradeSide, TradeStatus


@pytest.fixture
def opt_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.connect()
        yield db
        db.close()


def _populate_trades(db: Database, num_wins: int = 5, num_losses: int = 3):
    """Add some closed trades for testing."""
    for i in range(num_wins):
        t = Trade(
            id=f"win_{i}", ticker="AAPL", side=TradeSide.BUY,
            quantity=10, entry_price=150.0, stop_loss=144.0,
            take_profit=159.0, sentiment_score=0.8, confidence=0.7,
            reasoning="Test win", opened_at="2025-05-01T10:00:00",
        )
        db.insert_trade(t)
        db.close_trade(f"win_{i}", 158.0, TradeStatus.CLOSED, 80.0, "2025-05-03")

    for i in range(num_losses):
        t = Trade(
            id=f"loss_{i}", ticker="MSFT", side=TradeSide.BUY,
            quantity=10, entry_price=400.0, stop_loss=388.0,
            take_profit=418.0, sentiment_score=0.65, confidence=0.5,
            reasoning="Test loss", opened_at="2025-05-01T10:00:00",
        )
        db.insert_trade(t)
        db.close_trade(f"loss_{i}", 388.0, TradeStatus.STOPPED_OUT, -120.0, "2025-05-03")


class TestStrategyOptimizer:
    def test_skips_with_few_trades(self, opt_db):
        optimizer = StrategyOptimizer(opt_db)
        result = optimizer.run_daily_review()
        assert result["skipped"] is True
        assert result["reason"] == "insufficient_trades"

    @patch("src.adaptation.optimizer.subprocess.run")
    def test_applies_parameter_changes(self, mock_run, opt_db):
        _populate_trades(opt_db)
        opt_db.set_param("sentiment_buy_threshold", 0.6)

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "analysis": "Win rate is decent but losers are too big",
                "changes": [
                    {
                        "param": "sentiment_buy_threshold",
                        "old_value": 0.6,
                        "new_value": 0.65,
                        "reason": "Be more selective to filter weak signals"
                    }
                ]
            }),
            stderr="",
        )

        optimizer = StrategyOptimizer(opt_db)
        result = optimizer.run_daily_review()

        assert len(result["changes"]) == 1
        assert result["changes"][0]["param"] == "sentiment_buy_threshold"
        assert opt_db.get_param("sentiment_buy_threshold") == 0.65

    @patch("src.adaptation.optimizer.subprocess.run")
    def test_caps_large_changes(self, mock_run, opt_db):
        _populate_trades(opt_db)
        opt_db.set_param("sentiment_buy_threshold", 0.6)

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "analysis": "Needs big change",
                "changes": [
                    {
                        "param": "sentiment_buy_threshold",
                        "old_value": 0.6,
                        "new_value": 0.9,  # 50% change — exceeds 20% cap
                        "reason": "Test"
                    }
                ]
            }),
            stderr="",
        )

        optimizer = StrategyOptimizer(opt_db, max_change_pct=0.20)
        result = optimizer.run_daily_review()

        new_val = opt_db.get_param("sentiment_buy_threshold")
        # Should be capped at 20% increase: 0.6 + (0.6 * 0.20) = 0.72
        assert new_val == pytest.approx(0.72, abs=0.01)

    @patch("src.adaptation.optimizer.subprocess.run")
    def test_handles_claude_failure(self, mock_run, opt_db):
        _populate_trades(opt_db)

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        optimizer = StrategyOptimizer(opt_db)
        result = optimizer.run_daily_review()

        assert result["changes"] == []

    @patch("src.adaptation.optimizer.subprocess.run")
    def test_handles_bad_json(self, mock_run, opt_db):
        _populate_trades(opt_db)

        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")

        optimizer = StrategyOptimizer(opt_db)
        result = optimizer.run_daily_review()

        assert result["changes"] == []

    @patch("src.adaptation.optimizer.subprocess.run")
    def test_handles_timeout(self, mock_run, opt_db):
        import subprocess
        _populate_trades(opt_db)

        mock_run.side_effect = subprocess.TimeoutExpired("claude", 180)

        optimizer = StrategyOptimizer(opt_db)
        result = optimizer.run_daily_review()

        assert result["changes"] == []

    @patch("src.adaptation.optimizer.subprocess.run")
    def test_handles_no_changes(self, mock_run, opt_db):
        _populate_trades(opt_db)

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"analysis": "All good", "changes": []}),
            stderr="",
        )

        optimizer = StrategyOptimizer(opt_db)
        result = optimizer.run_daily_review()

        assert result["changes"] == []

    @patch("src.adaptation.optimizer.subprocess.run")
    def test_simulation_review(self, mock_run, opt_db):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "analysis": "Test",
                "changes": [
                    {"param": "rsi_overbought", "old_value": 70, "new_value": 72, "reason": "test"}
                ]
            }),
            stderr="",
        )

        opt_db.set_param("rsi_overbought", 70.0)

        optimizer = StrategyOptimizer(opt_db)
        stats = {"total": 10, "wins": 6, "losses": 4, "win_rate": 0.6, "avg_pnl": 25.0, "total_pnl": 250.0}
        result = optimizer.run_simulation_review(stats, [], {})

        assert len(result["changes"]) == 1
