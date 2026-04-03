"""Tests for deployment components — health check and main entry point."""
from __future__ import annotations

import json
import time
import urllib.request
from unittest.mock import patch, MagicMock

import pytest

from src.live.health import start_health_server, update_status, _status


class TestHealthServer:
    def test_health_endpoint(self):
        thread = start_health_server(port=18080)
        time.sleep(0.2)  # Give server time to start

        try:
            resp = urllib.request.urlopen("http://localhost:18080/health")
            assert resp.status == 200
            data = json.loads(resp.read())
            assert data["status"] == "running"
            assert "started_at" in data
        finally:
            # Server is daemon thread, will stop when test exits
            pass

    def test_404_on_unknown_path(self):
        # Server already running from previous test on 18080
        try:
            urllib.request.urlopen("http://localhost:18080/unknown")
            assert False, "Should have raised"
        except urllib.error.HTTPError as e:
            assert e.code == 404

    def test_update_status(self):
        update_status("last_call1", "2025-04-03T09:00:00")
        assert _status["last_call1"] == "2025-04-03T09:00:00"


class TestMainEntryPoint:
    @patch("src.live.main.create_scheduler")
    @patch("src.live.main.start_health_server")
    @patch("src.live.main.LiveOrchestrator")
    @patch("src.live.main.LiveUniverse")
    @patch("src.live.main.LiveWatchlist")
    @patch("src.live.main.TriggerCheck")
    @patch("src.live.main.LiveExecutor")
    @patch("src.live.main.EmailNotifier")
    @patch("src.live.main.DecisionEngine")
    @patch("src.live.main.ThesisManager")
    @patch("src.live.main.FundamentalsClient")
    @patch("src.live.main.TechnicalAnalyzer")
    @patch("src.live.main.Broker")
    @patch("src.live.main.MarketData")
    @patch("src.live.main.ClaudeClient")
    @patch("src.live.main.get_gmail_credentials", return_value=("test@gmail.com", "pass"))
    @patch("src.live.main.get_anthropic_key", return_value="test-key")
    @patch("src.live.main.get_alpaca_keys", return_value=("api", "secret"))
    def test_main_initializes_and_starts(
        self, mock_alpaca, mock_anthropic, mock_gmail,
        mock_claude, mock_market, mock_broker,
        mock_tech, mock_fund, mock_tm, mock_engine,
        mock_notifier, mock_executor, mock_trigger,
        mock_watchlist, mock_universe, mock_orch,
        mock_health, mock_scheduler,
    ):
        from src.live.main import main

        # Mock universe to appear empty (triggers first boot)
        mock_universe_instance = MagicMock()
        mock_universe_instance.__len__ = MagicMock(return_value=0)
        mock_universe.return_value = mock_universe_instance

        # Mock scheduler to not block
        mock_sched_instance = MagicMock()
        mock_scheduler.return_value = mock_sched_instance
        mock_sched_instance.start.side_effect = KeyboardInterrupt

        try:
            main()
        except SystemExit:
            pass

        # Verify key initialization happened
        mock_claude.assert_called_once()
        mock_market.assert_called_once()
        mock_broker.assert_called_once()
        mock_orch.assert_called_once()
        mock_health.assert_called_once()
        mock_scheduler.assert_called_once()

        # Verify startup sequence
        orch_instance = mock_orch.return_value
        orch_instance.reconcile_on_startup.assert_called_once()
        orch_instance.initialize_first_boot.assert_called_once()
