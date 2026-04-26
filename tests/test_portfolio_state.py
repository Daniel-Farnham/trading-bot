"""Tests for src/live/portfolio_state.py — the live portfolio snapshot helper."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.live.portfolio_state import (
    AccountState,
    Performance,
    PortfolioSnapshot,
    PositionRow,
    build_portfolio_snapshot,
)


def _make_market(account: dict, positions: list[dict], spy_price=510.0, spy_bars=None):
    """MagicMock MarketData with the given account/positions/SPY response."""
    mkt = MagicMock()
    mkt.get_account.return_value = account
    mkt.get_positions.return_value = positions
    mkt.get_latest_price.return_value = spy_price
    if spy_bars is None:
        # Default: 5% rise from start to end
        spy_bars = pd.DataFrame({"close": [500.0, 525.0]})
    mkt.get_bars.return_value = spy_bars
    return mkt


class TestBuildPortfolioSnapshot:
    def test_basic_account_fields(self, tmp_path):
        mkt = _make_market(
            account={"equity": 100000, "cash": 30000, "portfolio_value": 100000},
            positions=[],
        )
        snap = build_portfolio_snapshot(mkt, tmp_path, max_positions=8, min_cash_pct=0.05)
        assert snap.account.equity == 100000.0
        assert snap.account.cash == 30000.0
        assert snap.account.cash_reserve == 5000.0  # 5% of equity
        assert snap.account.available_for_new_buys == 25000.0  # 30k - 5k reserve
        assert snap.account.position_count == 0
        assert snap.account.max_positions == 8
        assert snap.account.at_max_positions is False
        assert snap.account.over_limit == 0

    def test_at_max_positions(self, tmp_path):
        positions = [{"ticker": f"T{i}", "qty": 1, "market_value": 1000} for i in range(8)]
        mkt = _make_market(
            account={"equity": 100000, "cash": 5000, "portfolio_value": 100000},
            positions=positions,
        )
        snap = build_portfolio_snapshot(mkt, tmp_path, max_positions=8, min_cash_pct=0.05)
        assert snap.account.position_count == 8
        assert snap.account.at_max_positions is True
        assert snap.account.over_limit == 0

    def test_over_limit_detected(self, tmp_path):
        positions = [{"ticker": f"T{i}", "qty": 1, "market_value": 1000} for i in range(10)]
        mkt = _make_market(
            account={"equity": 100000, "cash": -1000, "portfolio_value": 100000},
            positions=positions,
        )
        snap = build_portfolio_snapshot(mkt, tmp_path, max_positions=8, min_cash_pct=0.05)
        assert snap.account.over_limit == 2
        assert snap.account.at_max_positions is True

    def test_negative_cash_floors_buying_power_at_zero(self, tmp_path):
        # Reproduces the user's actual failure state: cash -$24k, equity 108k
        mkt = _make_market(
            account={"equity": 108384.53, "cash": -24144.61, "portfolio_value": 108384.53},
            positions=[],
        )
        snap = build_portfolio_snapshot(mkt, tmp_path, max_positions=8, min_cash_pct=0.05)
        assert snap.account.cash == -24144.61
        assert snap.account.available_for_new_buys == 0.0  # floored, not negative

    def test_position_row_fields(self, tmp_path):
        mkt = _make_market(
            account={"equity": 100000, "cash": 5000, "portfolio_value": 100000},
            positions=[{
                "ticker": "MU", "side": "long", "qty": 50,
                "avg_entry": 400.0, "current_price": 487.0,
                "market_value": 24350.0,
                "unrealized_pnl": 4350.0, "unrealized_pnl_pct": 0.2175,
                "change_today_pct": 0.012,
            }],
        )
        snap = build_portfolio_snapshot(mkt, tmp_path, max_positions=8, min_cash_pct=0.05)
        assert len(snap.positions) == 1
        r = snap.positions[0]
        assert r.ticker == "MU"
        assert r.qty == 50
        assert r.avg_entry == 400.0
        assert r.current_price == 487.0
        assert r.day_change_pct == 1.2  # 0.012 * 100
        assert r.unrealized_pnl == 4350.0
        assert r.unrealized_pnl_pct == 21.75
        assert r.pct_of_portfolio == 24.35  # 24350 / 100000

    def test_total_unrealized_pnl_aggregated(self, tmp_path):
        mkt = _make_market(
            account={"equity": 100000, "cash": 5000, "portfolio_value": 100000},
            positions=[
                {"ticker": "A", "qty": 1, "market_value": 5000, "unrealized_pnl": 500},
                {"ticker": "B", "qty": 1, "market_value": 3000, "unrealized_pnl": -200},
            ],
        )
        snap = build_portfolio_snapshot(mkt, tmp_path, max_positions=8, min_cash_pct=0.05)
        assert snap.performance.unrealized_pnl == 300.0

    def test_inception_default_when_file_missing(self, tmp_path):
        mkt = _make_market(
            account={"equity": 110000, "cash": 5000, "portfolio_value": 110000},
            positions=[],
        )
        snap = build_portfolio_snapshot(mkt, tmp_path, max_positions=8, min_cash_pct=0.05)
        # Default initial value is 100000; equity 110k → +10%
        assert snap.performance.initial_value == 100000.0
        assert snap.performance.total_return_pct == 10.0

    def test_inception_loaded_from_file(self, tmp_path):
        (tmp_path / "inception.json").write_text(
            '{"start_date": "2026-01-15", "initial_value": 50000}'
        )
        mkt = _make_market(
            account={"equity": 60000, "cash": 1000, "portfolio_value": 60000},
            positions=[],
        )
        snap = build_portfolio_snapshot(mkt, tmp_path, max_positions=8, min_cash_pct=0.05)
        assert snap.performance.inception_date == "2026-01-15"
        assert snap.performance.initial_value == 50000.0
        assert snap.performance.total_return_pct == 20.0

    def test_spy_return_computed(self, tmp_path):
        mkt = _make_market(
            account={"equity": 100000, "cash": 5000, "portfolio_value": 100000},
            positions=[],
            spy_bars=pd.DataFrame({"close": [500.0, 510.0]}),  # +2%
        )
        snap = build_portfolio_snapshot(mkt, tmp_path, max_positions=8, min_cash_pct=0.05)
        assert snap.performance.spy_return_pct == 2.0
        # Bot is flat (100k → 100k = 0%); SPY +2 → vs SPY = -2pp
        assert snap.performance.return_vs_spy == -2.0

    def test_spy_failure_yields_none(self, tmp_path):
        mkt = _make_market(
            account={"equity": 100000, "cash": 5000, "portfolio_value": 100000},
            positions=[],
            spy_bars=pd.DataFrame(),  # empty
        )
        snap = build_portfolio_snapshot(mkt, tmp_path, max_positions=8, min_cash_pct=0.05)
        assert snap.performance.spy_return_pct is None
        assert snap.performance.return_vs_spy is None


class TestDashboardDict:
    def test_keys_match_health_endpoint_contract(self):
        snap = PortfolioSnapshot(
            account=AccountState(100000, 30000, 5000, 25000, 3, 8, 0.05, False, 0),
            performance=Performance(5.0, 2.0, 3.0, 1500.0, "2026-04-05", 100000.0, 510.0),
            positions=[],
        )
        d = snap.to_dashboard_dict()
        # The dashboard JS at health.py:377-385 reads exactly these keys
        expected = {
            "equity", "cash", "total_return_pct", "unrealized_pnl",
            "position_count", "spy_price", "spy_return_pct",
            "inception_date", "initial_value",
        }
        assert set(d.keys()) == expected
        assert d["equity"] == 100000
        assert d["position_count"] == 3
        assert d["total_return_pct"] == 5.0
