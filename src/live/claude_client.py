"""Anthropic SDK client with hard spend caps.

Replaces subprocess calls to Claude CLI with direct API usage.
Tracks token usage and enforces daily/monthly budget limits.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

# Pricing per million tokens (input, output)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-opus-4-20250514": (15.0, 75.0),
    # Aliases
    "sonnet": (3.0, 15.0),
    "opus": (15.0, 75.0),
}

DEFAULT_MODEL = "claude-sonnet-4-20250514"


class BudgetExceededError(Exception):
    """Raised when daily or monthly spend cap is hit."""


class ClaudeClient:
    """Anthropic API wrapper with spend tracking and hard budget caps."""

    def __init__(
        self,
        api_key: str,
        daily_budget_usd: float = 2.00,
        monthly_budget_usd: float = 40.00,
        spend_log_path: str | Path = "data/live/api_spend.jsonl",
    ):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._daily_budget = daily_budget_usd
        self._monthly_budget = monthly_budget_usd
        self._spend_log = Path(spend_log_path)
        self._spend_log.parent.mkdir(parents=True, exist_ok=True)

    def call(
        self,
        prompt: str,
        system: str | None = None,
        model: str = DEFAULT_MODEL,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> dict | None:
        """Call Claude and return parsed JSON response.

        Args:
            prompt: User message content.
            system: Optional system prompt.
            model: Model identifier.
            tools: Optional list of tool definitions for tool use.
            max_tokens: Maximum output tokens.

        Returns:
            Parsed JSON dict from Claude's response, or None on failure.

        Raises:
            BudgetExceededError: If daily or monthly spend cap would be exceeded.
        """
        self._check_budget()

        messages = [{"role": "user", "content": prompt}]
        kwargs: dict = {
            "model": self._resolve_model(model),
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        total_input_tokens = 0
        total_output_tokens = 0

        try:
            response = self._client.messages.create(**kwargs)
            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            # Handle tool use loop
            if tools:
                while response.stop_reason == "tool_use":
                    tool_results = self._process_tool_calls(response)
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": tool_results})
                    kwargs["messages"] = messages

                    response = self._client.messages.create(**kwargs)
                    total_input_tokens += response.usage.input_tokens
                    total_output_tokens += response.usage.output_tokens

            # Extract text from response
            raw = ""
            for block in response.content:
                if block.type == "text":
                    raw += block.text

            if not raw:
                logger.error("Claude returned no text content")
                return None

            # Log spend
            cost = self._calculate_cost(
                model, total_input_tokens, total_output_tokens,
            )
            self._log_spend(model, total_input_tokens, total_output_tokens, cost)

            logger.debug(
                "Claude response (%d input, %d output tokens, $%.4f): %s",
                total_input_tokens, total_output_tokens, cost, raw[:300],
            )

            # Strip markdown code fences and parse JSON
            return self._parse_json_response(raw)

        except anthropic.APIError as e:
            logger.error("Anthropic API error: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error calling Claude: %s: %s", type(e).__name__, e)
            return None

    def get_daily_spend(self) -> float:
        """Sum today's spend from the log."""
        today = date.today().isoformat()
        return self._sum_spend(lambda entry: entry.get("date") == today)

    def get_monthly_spend(self) -> float:
        """Sum this month's spend from the log."""
        month_prefix = date.today().strftime("%Y-%m")
        return self._sum_spend(lambda entry: entry.get("date", "").startswith(month_prefix))

    def _check_budget(self) -> None:
        daily = self.get_daily_spend()
        if daily >= self._daily_budget:
            raise BudgetExceededError(
                f"Daily budget exceeded: ${daily:.2f} >= ${self._daily_budget:.2f}"
            )
        monthly = self.get_monthly_spend()
        if monthly >= self._monthly_budget:
            raise BudgetExceededError(
                f"Monthly budget exceeded: ${monthly:.2f} >= ${self._monthly_budget:.2f}"
            )

    def _resolve_model(self, model: str) -> str:
        aliases = {
            "sonnet": "claude-sonnet-4-20250514",
            "opus": "claude-opus-4-20250514",
        }
        return aliases.get(model, model)

    def _calculate_cost(
        self, model: str, input_tokens: int, output_tokens: int,
    ) -> float:
        resolved = self._resolve_model(model)
        input_price, output_price = MODEL_PRICING.get(
            resolved, MODEL_PRICING.get(model, (3.0, 15.0))
        )
        return (input_tokens * input_price + output_tokens * output_price) / 1_000_000

    def _log_spend(
        self, model: str, input_tokens: int, output_tokens: int, cost: float,
    ) -> None:
        entry = {
            "date": date.today().isoformat(),
            "timestamp": datetime.now().isoformat(),
            "model": self._resolve_model(model),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
        }
        with open(self._spend_log, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _sum_spend(self, predicate) -> float:
        if not self._spend_log.exists():
            return 0.0
        total = 0.0
        for line in self._spend_log.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if predicate(entry):
                    total += entry.get("cost_usd", 0.0)
            except json.JSONDecodeError:
                continue
        return total

    def _process_tool_calls(self, response) -> list[dict]:
        """Extract tool use blocks and return placeholder results.

        In production, this will be wired to actual MCP tool handlers.
        For now, returns an error message so the loop terminates gracefully.
        """
        results = []
        for block in response.content:
            if block.type == "tool_use":
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps({
                        "error": "Tool execution not yet configured",
                    }),
                })
        return results

    @staticmethod
    def _parse_json_response(raw: str) -> dict | None:
        """Strip markdown code fences and parse JSON."""
        text = raw
        if "```json" in text:
            text = text.split("```json", 1)[1]
            text = text.split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1]
            text = text.split("```", 1)[0]
        text = text.strip()

        if not text:
            logger.error(
                "Response contained no JSON after stripping fences.\n  Raw (first 1000): %s",
                raw[:1000],
            )
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse JSON: %s\n  Text (first 1000): %s",
                e, text[:1000],
            )
            return None
