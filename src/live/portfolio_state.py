"""Single source of truth for live portfolio state.

Pulls live Alpaca account + positions, performance vs SPY since inception,
and derives the constraints both the Call 3 prompt and the executor's
cash-math validator need to share.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_INCEPTION = {"start_date": "2026-04-05", "initial_value": 100000.0}


@dataclass
class PositionRow:
    ticker: str
    side: str
    qty: int
    avg_entry: float
    current_price: float
    market_value: float
    day_change_pct: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    pct_of_portfolio: float


@dataclass
class AccountState:
    equity: float
    cash: float
    cash_reserve: float
    available_for_new_buys: float
    position_count: int
    max_positions: int
    min_cash_pct: float
    at_max_positions: bool
    over_limit: int


@dataclass
class Performance:
    total_return_pct: float
    spy_return_pct: float | None
    return_vs_spy: float | None
    unrealized_pnl: float
    inception_date: str
    initial_value: float
    spy_price: float | None


@dataclass
class PortfolioSnapshot:
    account: AccountState
    performance: Performance
    positions: list[PositionRow] = field(default_factory=list)

    def to_dashboard_dict(self) -> dict:
        """Shape compatible with the existing /performance endpoint."""
        return {
            "equity": self.account.equity,
            "cash": self.account.cash,
            "total_return_pct": self.performance.total_return_pct,
            "unrealized_pnl": self.performance.unrealized_pnl,
            "position_count": self.account.position_count,
            "spy_price": self.performance.spy_price,
            "spy_return_pct": self.performance.spy_return_pct,
            "inception_date": self.performance.inception_date,
            "initial_value": self.performance.initial_value,
        }


def _load_inception(data_dir: str | Path) -> dict:
    path = Path(data_dir) / "inception.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:
            logger.warning("Failed to load inception.json: %s — using default", e)
    return dict(DEFAULT_INCEPTION)


def _spy_return_since(market_data, inception_date: str) -> float | None:
    try:
        inception_dt = datetime.fromisoformat(inception_date)
        bars = market_data.get_bars("SPY", start=inception_dt, limit=200)
        if bars.empty or len(bars) < 2:
            return None
        start = float(bars.iloc[0]["close"])
        end = float(bars.iloc[-1]["close"])
        return ((end - start) / start) * 100
    except Exception as e:
        logger.warning("Failed to compute SPY return: %s", e)
        return None


def build_portfolio_snapshot(
    market_data,
    data_dir: str | Path,
    max_positions: int,
    min_cash_pct: float,
) -> PortfolioSnapshot:
    """Build a complete portfolio snapshot from live Alpaca data.

    `max_positions` and `min_cash_pct` should be taken from the active
    RiskManagerV3 so prompt-displayed limits match what the executor enforces.
    """
    account = market_data.get_account()
    positions = market_data.get_positions()

    equity = float(account.get("equity", account.get("portfolio_value", 0)) or 0)
    cash = float(account.get("cash", 0) or 0)
    cash_reserve = round(equity * min_cash_pct, 2)
    available_for_new_buys = max(0.0, round(cash - cash_reserve, 2))

    position_count = len(positions)
    over_limit = max(0, position_count - max_positions)

    inception = _load_inception(data_dir)
    inception_date = inception.get("start_date", DEFAULT_INCEPTION["start_date"])
    initial_value = float(inception.get("initial_value", DEFAULT_INCEPTION["initial_value"]))
    total_return_pct = round(((equity - initial_value) / initial_value) * 100, 2) if initial_value > 0 else 0.0

    spy_price = market_data.get_latest_price("SPY")
    spy_return = _spy_return_since(market_data, inception_date)
    spy_return_pct = round(spy_return, 2) if spy_return is not None else None
    return_vs_spy = round(total_return_pct - spy_return_pct, 2) if spy_return_pct is not None else None

    rows: list[PositionRow] = []
    total_unrealized = 0.0
    for p in positions:
        market_value = float(p.get("market_value", 0))
        unrealized = float(p.get("unrealized_pnl", 0))
        total_unrealized += unrealized
        rows.append(PositionRow(
            ticker=p.get("ticker", ""),
            side=p.get("side", "long"),
            qty=int(p.get("qty", 0)),
            avg_entry=float(p.get("avg_entry", 0)),
            current_price=float(p.get("current_price", 0)),
            market_value=round(market_value, 2),
            day_change_pct=round(float(p.get("change_today_pct", 0)) * 100, 2),
            unrealized_pnl=round(unrealized, 2),
            unrealized_pnl_pct=round(float(p.get("unrealized_pnl_pct", 0)) * 100, 2),
            pct_of_portfolio=round((market_value / equity * 100) if equity > 0 else 0, 2),
        ))

    return PortfolioSnapshot(
        account=AccountState(
            equity=round(equity, 2),
            cash=round(cash, 2),
            cash_reserve=cash_reserve,
            available_for_new_buys=available_for_new_buys,
            position_count=position_count,
            max_positions=max_positions,
            min_cash_pct=min_cash_pct,
            at_max_positions=position_count >= max_positions,
            over_limit=over_limit,
        ),
        performance=Performance(
            total_return_pct=total_return_pct,
            spy_return_pct=spy_return_pct,
            return_vs_spy=return_vs_spy,
            unrealized_pnl=round(total_unrealized, 2),
            inception_date=inception_date,
            initial_value=initial_value,
            spy_price=spy_price,
        ),
        positions=rows,
    )
