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

        # Return the most recent one, enriched with growth trend from prior period
        candidates.sort(key=lambda q: q["date"], reverse=True)
        latest = candidates[0]

        # Add growth trend: compare current YoY growth to prior period's YoY growth
        if len(candidates) >= 2:
            prior = candidates[1]
            curr_rev_yoy = latest.get("revenue_growth_yoy")
            prev_rev_yoy = prior.get("revenue_growth_yoy")
            if curr_rev_yoy is not None and prev_rev_yoy is not None:
                if curr_rev_yoy > prev_rev_yoy + 2:
                    latest["revenue_trend"] = "accelerating"
                elif curr_rev_yoy < prev_rev_yoy - 2:
                    latest["revenue_trend"] = "decelerating"
                else:
                    latest["revenue_trend"] = "stable"
                latest["prev_revenue_growth_yoy"] = prev_rev_yoy

            curr_earn_yoy = latest.get("earnings_growth_yoy")
            prev_earn_yoy = prior.get("earnings_growth_yoy")
            if curr_earn_yoy is not None and prev_earn_yoy is not None:
                if curr_earn_yoy > prev_earn_yoy + 2:
                    latest["earnings_trend"] = "accelerating"
                elif curr_earn_yoy < prev_earn_yoy - 2:
                    latest["earnings_trend"] = "decelerating"
                else:
                    latest["earnings_trend"] = "stable"
                latest["prev_earnings_growth_yoy"] = prev_earn_yoy

        return latest

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
        """Build financial metrics from yfinance statements.

        Uses quarterly data as primary source, then backfills with annual data
        for dates not covered by quarterly (yfinance only gives ~5 quarters
        but ~5 years of annual data).
        """
        try:
            income = stock.quarterly_income_stmt
            balance = stock.quarterly_balance_sheet
        except Exception as e:
            logger.warning("Failed to get statements for %s: %s", ticker, e)
            income = None
            balance = None

        # Also fetch annual data for backfill
        try:
            annual_income = stock.income_stmt
            annual_balance = stock.balance_sheet
        except Exception:
            annual_income = None
            annual_balance = None

        if (income is None or income.empty) and (annual_income is None or annual_income.empty):
            return []

        if income is None or income.empty:
            # No quarterly data at all — use annual only
            return self._build_from_statements(
                annual_income, annual_balance, ticker, is_annual=True,
            )

        # Build quarterly first
        quarters = self._build_from_statements(income, balance, ticker, is_annual=False)

        # Backfill with annual data for dates not covered by quarterly
        if annual_income is not None and not annual_income.empty:
            quarterly_dates = {q["date"] for q in quarters}
            earliest_quarterly = min(quarterly_dates) if quarterly_dates else "9999"
            annual_periods = self._build_from_statements(
                annual_income, annual_balance, ticker, is_annual=True,
            )
            for ap in annual_periods:
                if ap["date"] < earliest_quarterly:
                    quarters.append(ap)

        quarters.sort(key=lambda q: q["date"])

        # Add Tier 3 metrics from .info snapshot
        self._enrich_with_tier3(stock, quarters)

        return quarters

    def _build_from_statements(
        self, income, balance, ticker: str, is_annual: bool = False,
    ) -> list[dict]:
        """Build financial metrics from income/balance sheet DataFrames.

        Works for both quarterly and annual statements.
        """
        if income is None or income.empty:
            return []

        annualize_factor = 1 if is_annual else 4  # For ROE annualization

        periods = []
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

            # ROE (annualized from quarterly, or direct from annual)
            roe = None
            if net_income is not None and equity and equity > 0:
                roe = round((net_income * annualize_factor) / equity * 100, 1)

            periods.append({
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
                "roe": roe,
                "is_profitable": net_income is not None and net_income > 0,
                "is_annual": is_annual,
            })

        periods.sort(key=lambda q: q["date"])

        # Revenue growth (period over period)
        for i in range(1, len(periods)):
            prev_rev = periods[i - 1].get("revenue")
            curr_rev = periods[i].get("revenue")
            if prev_rev and prev_rev > 0 and curr_rev is not None:
                periods[i]["revenue_growth"] = round(
                    (curr_rev - prev_rev) / prev_rev * 100, 1
                )
            else:
                periods[i]["revenue_growth"] = None
        if periods:
            periods[0]["revenue_growth"] = None

        # YoY growth — for quarterly: 4 periods back; for annual: 1 period back
        yoy_offset = 1 if is_annual else 4
        for i in range(yoy_offset, len(periods)):
            prev_rev = periods[i - yoy_offset].get("revenue")
            curr_rev = periods[i].get("revenue")
            if prev_rev and prev_rev > 0 and curr_rev is not None:
                periods[i]["revenue_growth_yoy"] = round(
                    (curr_rev - prev_rev) / prev_rev * 100, 1
                )
            else:
                periods[i]["revenue_growth_yoy"] = None
            prev_ni = periods[i - yoy_offset].get("net_income")
            curr_ni = periods[i].get("net_income")
            if prev_ni and prev_ni > 0 and curr_ni is not None:
                periods[i]["earnings_growth_yoy"] = round(
                    (curr_ni - prev_ni) / prev_ni * 100, 1
                )
            else:
                periods[i]["earnings_growth_yoy"] = None
        for i in range(min(yoy_offset, len(periods))):
            periods[i]["revenue_growth_yoy"] = None
            periods[i]["earnings_growth_yoy"] = None

        return periods

    def _enrich_with_tier3(self, stock: yf.Ticker, periods: list[dict]) -> None:
        """Add Tier 3 metrics from .info (current snapshot, applied to latest period)."""
        try:
            info = stock.info
            if periods:
                latest = periods[-1]
                latest["pe_ratio"] = info.get("trailingPE")
                latest["forward_pe"] = info.get("forwardPE")
                latest["ev_to_ebitda"] = info.get("enterpriseToEbitda")
                latest["short_pct_float"] = _to_pct(info.get("shortPercentOfFloat"))
                latest["insider_pct"] = _to_pct(info.get("heldPercentInsiders"))
                latest["market_cap"] = info.get("marketCap")
                fcf = info.get("freeCashflow")
                mcap = info.get("marketCap")
                if fcf is not None and mcap and mcap > 0:
                    latest["fcf_yield"] = round(fcf / mcap * 100, 1)
                else:
                    latest["fcf_yield"] = None
        except Exception:
            pass

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

    def is_large_cap(self, ticker: str, threshold: float = 100e9) -> bool:
        """Check if market cap exceeds threshold (default $100B).

        Large-cap companies should not be penalized by fundamentals data lag.
        """
        quarters = self._cache.get(ticker)
        if not quarters:
            return False
        latest = quarters[-1]
        mcap = latest.get("market_cap")
        if mcap is None:
            return False
        return mcap >= threshold


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

    # YoY revenue growth (preferred) with QoQ fallback + trend
    rev_yoy = fundamentals.get("revenue_growth_yoy")
    rev_qoq = fundamentals.get("revenue_growth")
    rev_trend = fundamentals.get("revenue_trend")
    if rev_yoy is not None:
        trend_str = ""
        if rev_trend:
            trend_str = f" {rev_trend[0].upper()}"  # A=accelerating, D=decelerating, S=stable
        parts.append(f"RevGr(YoY)={rev_yoy:+.1f}%{trend_str}")
    elif rev_qoq is not None:
        parts.append(f"RevGr(QoQ)={rev_qoq:+.1f}%")

    # YoY earnings growth + trend
    earnings_yoy = fundamentals.get("earnings_growth_yoy")
    earnings_trend = fundamentals.get("earnings_trend")
    if earnings_yoy is not None:
        trend_str = ""
        if earnings_trend:
            trend_str = f" {earnings_trend[0].upper()}"
        parts.append(f"EarnGr(YoY)={earnings_yoy:+.1f}%{trend_str}")

    margin = fundamentals.get("profit_margin")
    if margin is not None:
        parts.append(f"Margin={margin:.1f}%")
    else:
        parts.append("Margin=N/A")

    roe = fundamentals.get("roe")
    if roe is not None:
        parts.append(f"ROE={roe:.1f}%")

    de = fundamentals.get("debt_to_equity")
    if de is not None:
        parts.append(f"D/E={de:.2f}")

    ev_ebitda = fundamentals.get("ev_to_ebitda")
    if ev_ebitda is not None:
        parts.append(f"EV/EBITDA={ev_ebitda:.1f}")

    fcf_yield = fundamentals.get("fcf_yield")
    if fcf_yield is not None:
        parts.append(f"FCFYield={fcf_yield:.1f}%")

    short_pct = fundamentals.get("short_pct_float")
    if short_pct is not None:
        parts.append(f"Short={short_pct:.1f}%")

    # Market cap (formatted as readable)
    mcap = fundamentals.get("market_cap")
    if mcap is not None:
        if mcap >= 1e12:
            parts.append(f"MCap=${mcap/1e12:.1f}T")
        elif mcap >= 1e9:
            parts.append(f"MCap=${mcap/1e9:.0f}B")

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
