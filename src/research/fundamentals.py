"""Fundamentals data client — fetches and caches quarterly financial metrics.

Uses yfinance for current/recent data. Provides point-in-time lookups
for backtesting (returns most recent quarterly data BEFORE the given date).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

logger = logging.getLogger(__name__)

# Fields we extract from yfinance quarterly statements
_INCOME_FIELDS = ["Total Revenue", "Net Income", "EBITDA", "Operating Income"]
_BALANCE_FIELDS = ["Total Debt", "Stockholders Equity", "Total Assets"]


class FundamentalsCache:
    """On-disk JSON cache for quarterly fundamentals."""

    def __init__(self, cache_dir: str | Path = "data/fundamentals_cache"):
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, ticker: str) -> Path:
        return self._dir / f"{ticker.upper()}.json"

    def get(self, ticker: str) -> list[dict] | None:
        p = self._path(ticker)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
            return data if isinstance(data, list) else None
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, ticker: str, quarters: list[dict]) -> None:
        self._path(ticker).write_text(json.dumps(quarters, indent=2))


class FundamentalsClient:
    """Fetches Tier 1 + Tier 3 fundamental metrics via yfinance."""

    def __init__(self, cache_dir: str | Path = "data/fundamentals_cache"):
        self._cache = FundamentalsCache(cache_dir)

    def fetch_and_cache(self, ticker: str, force: bool = False) -> list[dict]:
        """Fetch quarterly fundamentals for a ticker. Returns list of quarter dicts.

        Each dict has: date, revenue, net_income, profit_margin, debt_to_equity,
        ev_to_ebitda, short_pct_float, insider_pct, pe_ratio, revenue_growth.
        """
        if not force:
            cached = self._cache.get(ticker)
            if cached:
                return cached

        try:
            stock = yf.Ticker(ticker)
            quarters = self._build_quarters(stock, ticker)
            if quarters:
                self._cache.put(ticker, quarters)
            return quarters
        except Exception as e:
            logger.warning("Failed to fetch fundamentals for %s: %s", ticker, e)
            return []

    # Earnings are typically reported 30-60 days after quarter end.
    # We use 45 days as a conservative buffer to avoid leaking unreported results.
    EARNINGS_REPORT_LAG_DAYS = 45

    def get_fundamentals_at_date(self, ticker: str, as_of: datetime | str) -> dict | None:
        """Point-in-time lookup: returns the most recent quarterly data BEFORE as_of.

        Applies a 45-day lag after quarter-end to account for earnings reporting
        delay. A quarter ending 2025-01-31 wouldn't be available until ~2025-03-17.
        This prevents future-knowledge leakage in backtesting.
        """
        if isinstance(as_of, str):
            as_of = datetime.strptime(as_of, "%Y-%m-%d")

        quarters = self._cache.get(ticker)
        if not quarters:
            quarters = self.fetch_and_cache(ticker)
        if not quarters:
            return None

        # Find quarters where the results would have been publicly available
        # (quarter end date + reporting lag < as_of)
        lag = timedelta(days=self.EARNINGS_REPORT_LAG_DAYS)
        candidates = [
            q for q in quarters
            if datetime.strptime(q["date"], "%Y-%m-%d") + lag <= as_of
        ]
        if not candidates:
            return None

        # Return the most recent one
        candidates.sort(key=lambda q: q["date"], reverse=True)
        return candidates[0]

    def get_current_ratios(self, ticker: str) -> dict | None:
        """Get current snapshot ratios from yfinance .info endpoint.

        Used for live trading (not backtesting).
        """
        try:
            info = yf.Ticker(ticker).info
            return {
                "ticker": ticker,
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "revenue_growth": _to_pct(info.get("revenueGrowth")),
                "profit_margin": _to_pct(info.get("profitMargins")),
                "debt_to_equity": info.get("debtToEquity"),
                "ev_to_ebitda": info.get("enterpriseToEbitda"),
                "short_pct_float": _to_pct(info.get("shortPercentOfFloat")),
                "insider_pct": _to_pct(info.get("heldPercentInsiders")),
                "free_cash_flow": info.get("freeCashflow"),
                "market_cap": info.get("marketCap"),
            }
        except Exception as e:
            logger.warning("Failed to get current ratios for %s: %s", ticker, e)
            return None

    def prefetch_universe(self, tickers: list[str], force: bool = False) -> dict[str, list[dict]]:
        """Bulk fetch and cache fundamentals for the full universe."""
        results = {}
        for ticker in tickers:
            data = self.fetch_and_cache(ticker, force=force)
            if data:
                results[ticker] = data
                logger.debug("Cached %d quarters for %s", len(data), ticker)
            else:
                logger.warning("No fundamentals data for %s", ticker)
        logger.info(
            "Prefetched fundamentals: %d/%d tickers with data",
            len(results), len(tickers),
        )
        return results

    def _build_quarters(self, stock: yf.Ticker, ticker: str) -> list[dict]:
        """Build quarterly metrics from yfinance financial statements."""
        try:
            income = stock.quarterly_income_stmt
            balance = stock.quarterly_balance_sheet
        except Exception as e:
            logger.warning("Failed to get statements for %s: %s", ticker, e)
            return []

        if income is None or income.empty:
            return []

        quarters = []
        for col in income.columns:
            date_str = str(col.date()) if hasattr(col, "date") else str(col)[:10]

            revenue = _safe_get(income, "Total Revenue", col)
            net_income = _safe_get(income, "Net Income", col)
            ebitda = _safe_get(income, "EBITDA", col)
            operating_income = _safe_get(income, "Operating Income", col)

            # Balance sheet (may have different dates, find closest)
            total_debt = None
            equity = None
            total_assets = None
            if balance is not None and not balance.empty:
                bs_col = _find_closest_column(balance, col)
                if bs_col is not None:
                    total_debt = _safe_get(balance, "Total Debt", bs_col)
                    equity = _safe_get(balance, "Stockholders Equity", bs_col)
                    total_assets = _safe_get(balance, "Total Assets", bs_col)

            # Compute ratios
            profit_margin = None
            if revenue and revenue > 0 and net_income is not None:
                profit_margin = round(net_income / revenue * 100, 1)

            debt_to_equity = None
            if total_debt is not None and equity and equity > 0:
                debt_to_equity = round(total_debt / equity, 2)

            quarters.append({
                "date": date_str,
                "ticker": ticker,
                "revenue": revenue,
                "net_income": net_income,
                "ebitda": ebitda,
                "operating_income": operating_income,
                "total_debt": total_debt,
                "equity": equity,
                "total_assets": total_assets,
                "profit_margin": profit_margin,
                "debt_to_equity": debt_to_equity,
                "is_profitable": net_income is not None and net_income > 0,
            })

        # Compute revenue growth (QoQ same quarter YoY not possible with 5Q)
        quarters.sort(key=lambda q: q["date"])
        for i in range(1, len(quarters)):
            prev_rev = quarters[i - 1].get("revenue")
            curr_rev = quarters[i].get("revenue")
            if prev_rev and prev_rev > 0 and curr_rev is not None:
                quarters[i]["revenue_growth"] = round(
                    (curr_rev - prev_rev) / prev_rev * 100, 1
                )
            else:
                quarters[i]["revenue_growth"] = None
        if quarters:
            quarters[0]["revenue_growth"] = None

        # Add Tier 3 metrics from .info (current snapshot only, applied to latest quarter)
        try:
            info = stock.info
            if quarters:
                latest = quarters[-1]
                latest["pe_ratio"] = info.get("trailingPE")
                latest["forward_pe"] = info.get("forwardPE")
                latest["ev_to_ebitda"] = info.get("enterpriseToEbitda")
                latest["short_pct_float"] = _to_pct(info.get("shortPercentOfFloat"))
                latest["insider_pct"] = _to_pct(info.get("heldPercentInsiders"))
        except Exception:
            pass

        return quarters

    def is_profitable(self, ticker: str, as_of: datetime | str | None = None) -> bool | None:
        """Check if a company is profitable. Returns None if no data."""
        if as_of:
            q = self.get_fundamentals_at_date(ticker, as_of)
        else:
            quarters = self._cache.get(ticker)
            q = quarters[-1] if quarters else None
        if q is None:
            return None
        return q.get("is_profitable", None)


def format_fundamentals_for_prompt(
    fundamentals: dict | None,
    ticker: str,
) -> str | None:
    """Format a single ticker's fundamentals as a compact prompt line.

    Returns None if no data available.
    """
    if not fundamentals:
        return None

    parts = [ticker]

    pe = fundamentals.get("pe_ratio")
    if pe is not None:
        parts.append(f"P/E={pe:.1f}")
    else:
        parts.append("P/E=N/A")

    rev_growth = fundamentals.get("revenue_growth")
    if rev_growth is not None:
        parts.append(f"RevGr={rev_growth:+.1f}%")

    margin = fundamentals.get("profit_margin")
    if margin is not None:
        parts.append(f"Margin={margin:.1f}%")
    else:
        parts.append("Margin=N/A")

    de = fundamentals.get("debt_to_equity")
    if de is not None:
        parts.append(f"D/E={de:.2f}")

    ev_ebitda = fundamentals.get("ev_to_ebitda")
    if ev_ebitda is not None:
        parts.append(f"EV/EBITDA={ev_ebitda:.1f}")

    short_pct = fundamentals.get("short_pct_float")
    if short_pct is not None:
        parts.append(f"Short={short_pct:.1f}%")

    insider = fundamentals.get("insider_pct")
    if insider is not None:
        parts.append(f"Insider={insider:.1f}%")

    profitable = fundamentals.get("is_profitable")
    if profitable is not None:
        parts.append("Profitable" if profitable else "UNPROFITABLE")

    return " | ".join(parts)


def build_fundamentals_prompt_section(
    client: FundamentalsClient,
    tickers: list[str],
    as_of: datetime | str | None = None,
) -> str:
    """Build the FUNDAMENTALS section for Claude's prompt."""
    lines = []
    for ticker in tickers:
        if as_of:
            data = client.get_fundamentals_at_date(ticker, as_of)
        else:
            data = client.get_current_ratios(ticker)
        line = format_fundamentals_for_prompt(data, ticker)
        if line:
            lines.append(line)

    if not lines:
        return "(No fundamental data available)"
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_get(df, field: str, col) -> float | None:
    """Safely get a value from a DataFrame, returning None if missing."""
    if field not in df.index:
        return None
    val = df.loc[field, col]
    try:
        import math
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _find_closest_column(df, target_col):
    """Find the closest column date in df to the target column date."""
    if df.empty:
        return None
    target_date = target_col.date() if hasattr(target_col, "date") else target_col
    best = None
    best_delta = None
    for col in df.columns:
        col_date = col.date() if hasattr(col, "date") else col
        delta = abs((col_date - target_date).days)
        if best_delta is None or delta < best_delta:
            best = col
            best_delta = delta
    # Only match if within 90 days
    if best_delta is not None and best_delta <= 90:
        return best
    return None


def _to_pct(val) -> float | None:
    """Convert a decimal ratio (0.15) to percentage (15.0)."""
    if val is None:
        return None
    try:
        return round(float(val) * 100, 1)
    except (TypeError, ValueError):
        return None
