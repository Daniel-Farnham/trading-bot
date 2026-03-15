"""Tests for the fundamentals client and integration."""
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.research.fundamentals import (
    FundamentalsCache,
    FundamentalsClient,
    build_fundamentals_prompt_section,
    format_fundamentals_for_prompt,
)


class TestFundamentalsCache:
    def test_put_and_get(self, tmp_path):
        cache = FundamentalsCache(cache_dir=tmp_path)
        data = [{"date": "2025-06-30", "revenue": 1000000, "is_profitable": True}]
        cache.put("NVDA", data)
        result = cache.get("NVDA")
        assert result == data

    def test_get_missing(self, tmp_path):
        cache = FundamentalsCache(cache_dir=tmp_path)
        assert cache.get("FAKE") is None

    def test_case_insensitive_filename(self, tmp_path):
        cache = FundamentalsCache(cache_dir=tmp_path)
        cache.put("nvda", [{"date": "2025-06-30"}])
        assert (tmp_path / "NVDA.json").exists()


class TestFormatFundamentals:
    def test_full_data(self):
        data = {
            "pe_ratio": 65.2,
            "revenue_growth": 14.3,
            "profit_margin": 26.1,
            "debt_to_equity": 0.41,
            "ev_to_ebitda": 45.2,
            "short_pct_float": 1.2,
            "insider_pct": 0.1,
            "is_profitable": True,
        }
        line = format_fundamentals_for_prompt(data, "NVDA")
        assert "NVDA" in line
        assert "P/E=65.2" in line
        assert "Margin=26.1%" in line
        assert "D/E=0.41" in line
        assert "Profitable" in line

    def test_unprofitable(self):
        data = {
            "pe_ratio": None,
            "profit_margin": -5.2,
            "is_profitable": False,
        }
        line = format_fundamentals_for_prompt(data, "COIN")
        assert "UNPROFITABLE" in line
        assert "P/E=N/A" in line

    def test_none_data(self):
        assert format_fundamentals_for_prompt(None, "X") is None


class TestProfitabilityGate:
    """Test that unprofitable companies get capped at 'high' confidence."""

    def test_unprofitable_capped(self):
        from src.strategy.risk_v3 import RiskManagerV3

        rm = RiskManagerV3()
        plan = rm.evaluate_new_position(
            ticker="COIN",
            side="LONG",
            allocation_pct=15,
            price=100.0,
            portfolio_value=100_000,
            cash=80_000,
            open_position_count=0,
            existing_tickers=[],
            confidence="highest",
            is_profitable=False,
        )
        # Should be capped at 10% (high), not 15% (highest)
        assert hasattr(plan, "allocation_pct")
        assert plan.allocation_pct <= 10.0

    def test_profitable_gets_highest(self):
        from src.strategy.risk_v3 import RiskManagerV3

        rm = RiskManagerV3()
        plan = rm.evaluate_new_position(
            ticker="NVDA",
            side="LONG",
            allocation_pct=15,
            price=100.0,
            portfolio_value=100_000,
            cash=80_000,
            open_position_count=0,
            existing_tickers=[],
            confidence="highest",
            is_profitable=True,
        )
        assert hasattr(plan, "allocation_pct")
        assert plan.allocation_pct == 15.0

    def test_none_profitability_no_gate(self):
        """When profitability is unknown, don't gate."""
        from src.strategy.risk_v3 import RiskManagerV3

        rm = RiskManagerV3()
        plan = rm.evaluate_new_position(
            ticker="NEW",
            side="LONG",
            allocation_pct=15,
            price=100.0,
            portfolio_value=100_000,
            cash=80_000,
            open_position_count=0,
            existing_tickers=[],
            confidence="highest",
            is_profitable=None,
        )
        assert hasattr(plan, "allocation_pct")
        assert plan.allocation_pct == 15.0


class TestPointInTimeLookup:
    def test_returns_quarter_with_reporting_lag(self, tmp_path):
        cache = FundamentalsCache(cache_dir=tmp_path)
        quarters = [
            {"date": "2025-03-31", "revenue": 100, "is_profitable": True},
            {"date": "2025-06-30", "revenue": 120, "is_profitable": True},
            {"date": "2025-09-30", "revenue": 140, "is_profitable": False},
        ]
        cache.put("TEST", quarters)

        client = FundamentalsClient(cache_dir=tmp_path)
        # Aug 15 is only 46 days after June 30 — just past the 45-day lag
        # So Q2 (June 30) should be available, but Q3 (Sept 30) should not
        result = client.get_fundamentals_at_date("TEST", "2025-08-15")
        assert result["date"] == "2025-06-30"
        assert result["revenue"] == 120

    def test_reporting_lag_prevents_early_access(self, tmp_path):
        cache = FundamentalsCache(cache_dir=tmp_path)
        quarters = [
            {"date": "2025-06-30", "revenue": 120, "is_profitable": True},
        ]
        cache.put("TEST", quarters)

        client = FundamentalsClient(cache_dir=tmp_path)
        # July 15 is only 15 days after June 30 — earnings not reported yet
        result = client.get_fundamentals_at_date("TEST", "2025-07-15")
        assert result is None

    def test_no_future_leakage(self, tmp_path):
        cache = FundamentalsCache(cache_dir=tmp_path)
        quarters = [
            {"date": "2025-06-30", "revenue": 120, "is_profitable": True},
        ]
        cache.put("TEST", quarters)

        client = FundamentalsClient(cache_dir=tmp_path)
        # Looking up before any data exists
        result = client.get_fundamentals_at_date("TEST", "2025-01-01")
        assert result is None
