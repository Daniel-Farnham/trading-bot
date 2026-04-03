"""Tests for research tools (Call 1 discovery tools)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.live.research_tools import ResearchToolExecutor, RESEARCH_TOOLS


@pytest.fixture
def executor():
    news = MagicMock()
    market = MagicMock()
    technicals = MagicMock()
    fundamentals = MagicMock()
    return ResearchToolExecutor(
        news_client=news,
        market_data=market,
        technical_analyzer=technicals,
        fundamentals_client=fundamentals,
    )


class TestToolDefinitions:
    def test_all_tools_have_required_fields(self):
        for tool in RESEARCH_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_expected_tools_present(self):
        names = {t["name"] for t in RESEARCH_TOOLS}
        assert "search_news" in names
        assert "get_fundamentals" in names
        assert "get_price_action" in names
        assert "get_technicals" in names
        assert "screen_by_theme" in names


class TestSearchNews:
    def test_returns_articles(self, executor):
        executor._news.get_news.return_value = [
            {"title": "NVDA beats earnings", "publishedDate": "2026-04-03", "tickers": ["NVDA"], "description": "Strong Q1"},
            {"title": "MU demand surges", "publishedDate": "2026-04-03", "tickers": ["MU"], "description": "AI memory"},
        ]

        result_str = executor.execute("search_news", {"symbols": ["NVDA", "MU"], "limit": 5})
        result = json.loads(result_str)

        assert result["count"] == 2
        assert result["articles"][0]["title"] == "NVDA beats earnings"
        executor._news.get_news.assert_called_once()

    def test_empty_results(self, executor):
        executor._news.get_news.return_value = []

        result = json.loads(executor.execute("search_news", {}))
        assert result["count"] == 0
        assert result["articles"] == []


class TestGetFundamentals:
    @patch("yfinance.Ticker")
    def test_returns_fundamental_data(self, mock_yf_ticker, executor):
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "longName": "Micron Technology",
            "sector": "Technology",
            "industry": "Semiconductors",
            "marketCap": 150000000000,
            "trailingPE": 17.3,
            "revenueGrowth": 1.96,
            "profitMargins": 0.415,
            "debtToEquity": 25.0,
        }
        mock_yf_ticker.return_value = mock_ticker

        result = json.loads(executor.execute("get_fundamentals", {"ticker": "MU"}))

        assert result["ticker"] == "MU"
        assert result["name"] == "Micron Technology"
        assert result["revenue_growth_yoy"] == 1.96
        assert result["profit_margin"] == 0.415

    @patch("yfinance.Ticker")
    def test_handles_missing_data(self, mock_yf_ticker, executor):
        mock_ticker = MagicMock()
        mock_ticker.info = {"longName": "Unknown Corp"}
        mock_yf_ticker.return_value = mock_ticker

        result = json.loads(executor.execute("get_fundamentals", {"ticker": "XYZ"}))
        assert result["ticker"] == "XYZ"
        assert result.get("pe_ratio") is None


class TestGetPriceAction:
    def test_returns_price_data(self, executor):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=90, freq="D")
        bars = pd.DataFrame({
            "close": np.linspace(100, 150, 90),
            "high": np.linspace(102, 155, 90),
            "low": np.linspace(98, 145, 90),
            "volume": [1000000] * 90,
        }, index=dates)
        executor._market.get_bars.return_value = bars

        result = json.loads(executor.execute("get_price_action", {"ticker": "NVDA"}))

        assert result["ticker"] == "NVDA"
        assert result["current_price"] > 0
        assert result["high_52w"] > 0
        assert "1w" in result["returns"]
        assert "1m" in result["returns"]

    def test_empty_bars(self, executor):
        import pandas as pd
        executor._market.get_bars.return_value = pd.DataFrame()

        result = json.loads(executor.execute("get_price_action", {"ticker": "XYZ"}))
        assert "error" in result


class TestGetTechnicals:
    def test_returns_technical_data(self, executor):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=60, freq="D")
        bars = pd.DataFrame({
            "close": np.linspace(100, 150, 60),
            "high": np.linspace(102, 155, 60),
            "low": np.linspace(98, 145, 60),
            "volume": [1000000] * 60,
            "open": np.linspace(99, 149, 60),
        }, index=dates)
        executor._market.get_bars.return_value = bars

        mock_snap = MagicMock()
        mock_snap.close = 150.0
        mock_snap.rsi = 55.0
        mock_snap.macd_signal = "bullish"
        mock_snap.sma50 = 130.0
        mock_snap.obv_trend = "rising"
        mock_snap.atr_pct = 2.5
        mock_snap.hv_percentile = 45.0
        mock_snap.adx = 28.0
        executor._technicals.analyze.return_value = mock_snap

        result = json.loads(executor.execute("get_technicals", {"ticker": "NVDA"}))

        assert result["ticker"] == "NVDA"
        assert result["rsi"] == 55.0
        assert result["macd"] == "bullish"
        assert result["obv_trend"] == "rising"
        assert result["above_sma50"] is True


class TestScreenByTheme:
    @patch("yfinance.Ticker")
    def test_screens_for_semiconductor(self, mock_yf_ticker, executor):
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "longName": "Micron Technology",
            "industry": "Semiconductors",
            "marketCap": 150000000000,
            "trailingPE": 17.3,
            "revenueGrowth": 1.96,
        }
        mock_yf_ticker.return_value = mock_ticker

        result = json.loads(executor.execute("screen_by_theme", {"theme": "semiconductor"}))

        assert result["theme"] == "semiconductor"
        assert result["count"] > 0
        assert any(s["ticker"] == "NVDA" for s in result["stocks"])

    def test_unknown_theme(self, executor):
        result = json.loads(executor.execute("screen_by_theme", {"theme": "underwater basket weaving"}))

        assert result["stocks"] == []
        assert "note" in result


class TestUnknownTool:
    def test_returns_error(self, executor):
        result = json.loads(executor.execute("nonexistent_tool", {}))
        assert "error" in result


class TestToolExecutorLogging:
    def test_logs_tool_calls(self, executor, caplog):
        import logging
        executor._news.get_news.return_value = []

        with caplog.at_level(logging.INFO):
            executor.execute("search_news", {"symbols": ["NVDA"]})

        assert any("TOOL CALL: search_news" in r.message for r in caplog.records)
        assert any("TOOL RESULT: search_news" in r.message for r in caplog.records)


class TestClaudeClientToolIntegration:
    def test_tool_executor_called_during_tool_loop(self):
        """Verify ClaudeClient passes tool calls to the executor."""
        from src.live.claude_client import ClaudeClient

        with patch("src.live.claude_client.anthropic.Anthropic") as mock_anthropic:
            client = ClaudeClient(api_key="test", spend_log_path="/tmp/test_spend.jsonl")

            # First response: tool use
            tool_block = MagicMock()
            tool_block.type = "tool_use"
            tool_block.id = "tool_1"
            tool_block.name = "get_fundamentals"
            tool_block.input = {"ticker": "NVDA"}

            first_response = MagicMock()
            first_response.usage.input_tokens = 100
            first_response.usage.output_tokens = 50
            first_response.stop_reason = "tool_use"
            first_response.content = [tool_block]

            # Second response: text
            text_block = MagicMock()
            text_block.type = "text"
            text_block.text = '{"macro_assessment": "test"}'
            second_response = MagicMock()
            second_response.usage.input_tokens = 200
            second_response.usage.output_tokens = 100
            second_response.stop_reason = "end_turn"
            second_response.content = [text_block]

            client._client.messages.create.side_effect = [first_response, second_response]

            # Mock tool executor
            mock_executor = MagicMock()
            mock_executor.execute.return_value = '{"ticker": "NVDA", "pe_ratio": 25}'

            result = client.call(
                "test prompt",
                tools=[{"name": "get_fundamentals"}],
                tool_executor=mock_executor,
            )

            # Verify executor was called
            mock_executor.execute.assert_called_once_with("get_fundamentals", {"ticker": "NVDA"})
            assert result == {"macro_assessment": "test"}
