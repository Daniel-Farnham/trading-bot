"""Tests for the Anthropic SDK client with spend tracking and budget caps."""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.live.claude_client import ClaudeClient, BudgetExceededError


@pytest.fixture
def spend_log(tmp_path):
    return tmp_path / "api_spend.jsonl"


@pytest.fixture
def client(spend_log):
    with patch("src.live.claude_client.anthropic.Anthropic"):
        return ClaudeClient(
            api_key="test-key",
            daily_budget_usd=2.00,
            monthly_budget_usd=40.00,
            spend_log_path=spend_log,
        )


def _mock_response(text: str, input_tokens: int = 100, output_tokens: int = 50):
    """Create a mock Anthropic API response."""
    response = MagicMock()
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    response.stop_reason = "end_turn"
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response.content = [text_block]
    return response


class TestCall:
    def test_successful_json_response(self, client):
        data = {"world_assessment": "Markets are volatile"}
        client._client.messages.create.return_value = _mock_response(
            json.dumps(data),
        )

        result = client.call("test prompt")

        assert result == data
        client._client.messages.create.assert_called_once()

    def test_json_in_code_fence(self, client):
        data = {"status": "ok"}
        client._client.messages.create.return_value = _mock_response(
            f"```json\n{json.dumps(data)}\n```",
        )

        result = client.call("test prompt")
        assert result == data

    def test_json_in_bare_fence(self, client):
        data = {"status": "ok"}
        client._client.messages.create.return_value = _mock_response(
            f"```\n{json.dumps(data)}\n```",
        )

        result = client.call("test prompt")
        assert result == data

    def test_empty_response_returns_none(self, client):
        client._client.messages.create.return_value = _mock_response("")

        result = client.call("test prompt")
        assert result is None

    def test_invalid_json_returns_none(self, client):
        client._client.messages.create.return_value = _mock_response(
            "this is not json",
        )

        result = client.call("test prompt")
        assert result is None

    def test_passes_system_prompt(self, client):
        data = {"ok": True}
        client._client.messages.create.return_value = _mock_response(
            json.dumps(data),
        )

        client.call("test", system="You are a trader")

        kwargs = client._client.messages.create.call_args
        assert kwargs[1]["system"] == "You are a trader"

    def test_passes_tools(self, client):
        data = {"ok": True}
        client._client.messages.create.return_value = _mock_response(
            json.dumps(data),
        )

        tools = [{"name": "get_news", "description": "Fetch news"}]
        client.call("test", tools=tools)

        kwargs = client._client.messages.create.call_args
        assert kwargs[1]["tools"] == tools

    def test_model_alias_resolution(self, client):
        data = {"ok": True}
        client._client.messages.create.return_value = _mock_response(
            json.dumps(data),
        )

        client.call("test", model="sonnet")

        kwargs = client._client.messages.create.call_args
        assert kwargs[1]["model"] == "claude-sonnet-4-20250514"

    def test_model_alias_opus(self, client):
        data = {"ok": True}
        client._client.messages.create.return_value = _mock_response(
            json.dumps(data),
        )

        client.call("test", model="opus")

        kwargs = client._client.messages.create.call_args
        assert kwargs[1]["model"] == "claude-opus-4-20250514"

    def test_full_model_name_passed_through(self, client):
        data = {"ok": True}
        client._client.messages.create.return_value = _mock_response(
            json.dumps(data),
        )

        client.call("test", model="claude-sonnet-4-20250514")

        kwargs = client._client.messages.create.call_args
        assert kwargs[1]["model"] == "claude-sonnet-4-20250514"


class TestToolUseLoop:
    def test_handles_tool_use_then_text(self, client):
        # First response: tool use
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tool_123"
        tool_block.name = "get_news"
        tool_block.input = {"query": "NVDA"}

        first_response = MagicMock()
        first_response.usage.input_tokens = 100
        first_response.usage.output_tokens = 50
        first_response.stop_reason = "tool_use"
        first_response.content = [tool_block]

        # Second response: text
        data = {"assessment": "NVDA is bullish"}
        second_response = _mock_response(json.dumps(data), 200, 100)

        client._client.messages.create.side_effect = [first_response, second_response]

        result = client.call("test", tools=[{"name": "get_news"}])

        assert result == data
        assert client._client.messages.create.call_count == 2

    def test_tool_loop_accumulates_tokens(self, client, spend_log):
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tool_1"
        tool_block.name = "get_quote"
        tool_block.input = {}

        first = MagicMock()
        first.usage.input_tokens = 100
        first.usage.output_tokens = 50
        first.stop_reason = "tool_use"
        first.content = [tool_block]

        second = _mock_response(json.dumps({"ok": True}), 200, 100)
        client._client.messages.create.side_effect = [first, second]

        client.call("test", tools=[{"name": "get_quote"}])

        # Check spend log has combined tokens
        entries = [json.loads(l) for l in spend_log.read_text().splitlines()]
        assert len(entries) == 1
        assert entries[0]["input_tokens"] == 300  # 100 + 200
        assert entries[0]["output_tokens"] == 150  # 50 + 100


class TestSpendTracking:
    def test_logs_spend_to_file(self, client, spend_log):
        client._client.messages.create.return_value = _mock_response(
            json.dumps({"ok": True}), input_tokens=1000, output_tokens=500,
        )

        client.call("test")

        entries = [json.loads(l) for l in spend_log.read_text().splitlines()]
        assert len(entries) == 1
        assert entries[0]["date"] == date.today().isoformat()
        assert entries[0]["input_tokens"] == 1000
        assert entries[0]["output_tokens"] == 500
        assert entries[0]["cost_usd"] > 0

    def test_cost_calculation_sonnet(self, client):
        # Sonnet: $3/MTok input, $15/MTok output
        cost = client._calculate_cost("sonnet", 1_000_000, 1_000_000)
        assert cost == pytest.approx(18.0)  # $3 + $15

    def test_cost_calculation_opus(self, client):
        # Opus: $15/MTok input, $75/MTok output
        cost = client._calculate_cost("opus", 1_000_000, 1_000_000)
        assert cost == pytest.approx(90.0)  # $15 + $75

    def test_cost_calculation_small_call(self, client):
        # Typical Call 1: ~5k input, ~1k output on Sonnet
        cost = client._calculate_cost("sonnet", 5000, 1000)
        assert cost == pytest.approx(0.030)  # $0.015 + $0.015

    def test_get_daily_spend(self, client, spend_log):
        today = date.today().isoformat()
        entries = [
            {"date": today, "cost_usd": 0.05},
            {"date": today, "cost_usd": 0.09},
            {"date": "2020-01-01", "cost_usd": 1.00},
        ]
        spend_log.write_text("\n".join(json.dumps(e) for e in entries))

        assert client.get_daily_spend() == pytest.approx(0.14)

    def test_get_monthly_spend(self, client, spend_log):
        month = date.today().strftime("%Y-%m")
        entries = [
            {"date": f"{month}-01", "cost_usd": 0.50},
            {"date": f"{month}-15", "cost_usd": 0.75},
            {"date": "2020-01-01", "cost_usd": 5.00},
        ]
        spend_log.write_text("\n".join(json.dumps(e) for e in entries))

        assert client.get_monthly_spend() == pytest.approx(1.25)

    def test_empty_log_returns_zero(self, client):
        assert client.get_daily_spend() == 0.0
        assert client.get_monthly_spend() == 0.0


class TestBudgetCaps:
    def test_daily_cap_blocks_call(self, client, spend_log):
        today = date.today().isoformat()
        entries = [{"date": today, "cost_usd": 2.00}]
        spend_log.write_text("\n".join(json.dumps(e) for e in entries))

        with pytest.raises(BudgetExceededError, match="Daily budget exceeded"):
            client.call("test prompt")

    def test_monthly_cap_blocks_call(self, client, spend_log):
        month = date.today().strftime("%Y-%m")
        entries = [{"date": f"{month}-01", "cost_usd": 40.00}]
        spend_log.write_text("\n".join(json.dumps(e) for e in entries))

        with pytest.raises(BudgetExceededError, match="Monthly budget exceeded"):
            client.call("test prompt")

    def test_under_budget_allows_call(self, client, spend_log):
        today = date.today().isoformat()
        entries = [{"date": today, "cost_usd": 0.50}]
        spend_log.write_text("\n".join(json.dumps(e) for e in entries))

        client._client.messages.create.return_value = _mock_response(
            json.dumps({"ok": True}),
        )

        result = client.call("test prompt")
        assert result == {"ok": True}

    def test_daily_cap_checked_before_api_call(self, client, spend_log):
        today = date.today().isoformat()
        entries = [{"date": today, "cost_usd": 2.00}]
        spend_log.write_text("\n".join(json.dumps(e) for e in entries))

        with pytest.raises(BudgetExceededError):
            client.call("test prompt")

        # API should never have been called
        client._client.messages.create.assert_not_called()


class TestParseJsonResponse:
    def test_plain_json(self):
        result = ClaudeClient._parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_code_fence(self):
        result = ClaudeClient._parse_json_response('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_bare_code_fence(self):
        result = ClaudeClient._parse_json_response('```\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_text_before_fence(self):
        result = ClaudeClient._parse_json_response(
            'Here is the response:\n```json\n{"key": "value"}\n```'
        )
        assert result == {"key": "value"}

    def test_empty_string(self):
        assert ClaudeClient._parse_json_response("") is None

    def test_invalid_json(self):
        assert ClaudeClient._parse_json_response("not json") is None

    def test_empty_after_fence_strip(self):
        assert ClaudeClient._parse_json_response("```json\n\n```") is None


class TestDecisionEngineSDKDelegation:
    """Test that DecisionEngine delegates to ClaudeClient when provided."""

    def test_uses_sdk_client_when_provided(self):
        from src.strategy.decision_engine import DecisionEngine
        from src.strategy.thesis_manager import ThesisManager

        mock_client = MagicMock()
        mock_client.call.return_value = {
            "world_assessment": "via SDK",
            "thesis_updates": [],
            "new_positions": [],
            "close_positions": [],
            "reduce_positions": [],
            "lessons": [],
            "weekly_summary": "SDK test",
        }

        mgr = MagicMock(spec=ThesisManager)
        mgr.get_decision_context.return_value = "memory"
        engine = DecisionEngine(
            thesis_manager=mgr, claude_client=mock_client,
        )

        result = engine._call_claude("test prompt")

        mock_client.call.assert_called_once_with("test prompt", model="sonnet")
        assert result["world_assessment"] == "via SDK"

    def test_falls_back_to_subprocess_when_no_client(self):
        from src.strategy.decision_engine import DecisionEngine
        from src.strategy.thesis_manager import ThesisManager

        mgr = MagicMock(spec=ThesisManager)
        engine = DecisionEngine(thesis_manager=mgr)

        assert engine._claude_client is None

        with patch("src.strategy.decision_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"world_assessment": "via CLI"}),
                stderr="",
            )
            result = engine._call_claude("test prompt")

            mock_run.assert_called_once()
            assert result["world_assessment"] == "via CLI"

    def test_sdk_client_none_response(self):
        from src.strategy.decision_engine import DecisionEngine
        from src.strategy.thesis_manager import ThesisManager

        mock_client = MagicMock()
        mock_client.call.return_value = None

        mgr = MagicMock(spec=ThesisManager)
        engine = DecisionEngine(
            thesis_manager=mgr, claude_client=mock_client,
        )

        result = engine._call_claude("test prompt")
        assert result is None


class TestBeliefConsolidatorSDKDelegation:
    """Test that consolidate_beliefs delegates to ClaudeClient when provided."""

    def test_uses_sdk_client_when_provided(self, tmp_path):
        from src.strategy.belief_consolidator import consolidate_beliefs

        mock_client = MagicMock()
        mock_client.call.return_value = {
            "seed_beliefs": [
                {
                    "name": "Trend Following",
                    "regime_count": 3,
                    "description": "Follow institutional flow.",
                },
            ],
            "reasoning": "Validated across regimes",
        }

        result = consolidate_beliefs(
            run_lessons=[{"content": "lesson1", "score": 4}],
            run_beliefs=[],
            run_regime="bull",
            run_summary={"period": "2024", "total_return_pct": 25},
            claude_client=mock_client,
        )

        mock_client.call.assert_called_once()
        assert len(result) == 1
        assert result[0]["name"] == "Trend Following"

    def test_falls_back_to_subprocess_when_no_client(self):
        from src.strategy.belief_consolidator import consolidate_beliefs

        with patch("src.strategy.belief_consolidator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "seed_beliefs": [
                        {"name": "Test", "regime_count": 1, "description": "desc"},
                    ],
                    "reasoning": "test",
                }),
                stderr="",
            )

            result = consolidate_beliefs(
                run_lessons=[],
                run_beliefs=[],
                run_regime="bull",
                run_summary={"period": "2024"},
            )

            mock_run.assert_called_once()
            assert len(result) == 1

    def test_sdk_failure_returns_existing(self, tmp_path):
        from src.strategy import belief_consolidator
        from src.strategy.belief_consolidator import consolidate_beliefs

        # Point to a non-existent seed beliefs file so existing = []
        original_path = belief_consolidator.SEED_BELIEFS_PATH
        belief_consolidator.SEED_BELIEFS_PATH = tmp_path / "seed_beliefs.md"

        try:
            mock_client = MagicMock()
            mock_client.call.return_value = None

            result = consolidate_beliefs(
                run_lessons=[],
                run_beliefs=[],
                run_regime="bull",
                run_summary={},
                claude_client=mock_client,
            )

            # Should return existing beliefs (empty since no seed file)
            assert result == []
        finally:
            belief_consolidator.SEED_BELIEFS_PATH = original_path
