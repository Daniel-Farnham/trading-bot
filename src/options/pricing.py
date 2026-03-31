"""Black-Scholes options pricing engine.

Provides synthetic options pricing using historical volatility (already
computed in TechnicalAnalyzer). No external options data needed.

Limitations vs real markets:
- Uses HV instead of IV (underprices by ~10-20%)
- No volatility skew (OTM puts underpriced)
- No event-driven IV spikes (pre-earnings, etc.)
Good enough for testing thesis quality in sim; upgrade to Polygon.io for precision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass
class OptionGreeks:
    """Greeks for a single option contract."""
    delta: float
    gamma: float
    theta: float  # Per day
    vega: float   # Per 1% IV change


@dataclass
class OptionQuote:
    """Synthetic price quote for an option."""
    premium: float
    greeks: OptionGreeks
    intrinsic: float
    time_value: float


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution (approximation)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    """Compute d1 and d2 for Black-Scholes."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0, 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def price_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes call price.

    Args:
        S: Current underlying price
        K: Strike price
        T: Time to expiry in years (e.g., 0.5 = 6 months)
        r: Risk-free rate (e.g., 0.045 = 4.5%)
        sigma: Volatility as decimal (e.g., 0.35 = 35%)
    """
    if T <= 0:
        return max(0.0, S - K)
    d1, d2 = _d1d2(S, K, T, r, sigma)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def price_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes put price."""
    if T <= 0:
        return max(0.0, K - S)
    d1, d2 = _d1d2(S, K, T, r, sigma)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def price_option(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "CALL",
) -> float:
    """Price a call or put."""
    if option_type.upper() == "CALL":
        return price_call(S, K, T, r, sigma)
    return price_put(S, K, T, r, sigma)


def greeks(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "CALL",
) -> OptionGreeks:
    """Compute Greeks for a call or put."""
    if T <= 0 or sigma <= 0 or S <= 0:
        sign = 1.0 if option_type.upper() == "CALL" else -1.0
        itm = (S > K) if option_type.upper() == "CALL" else (S < K)
        return OptionGreeks(
            delta=sign if itm else 0.0,
            gamma=0.0, theta=0.0, vega=0.0,
        )

    d1, d2 = _d1d2(S, K, T, r, sigma)
    sqrt_T = math.sqrt(T)

    # Common terms
    nd1 = _norm_pdf(d1)
    Nd1 = _norm_cdf(d1)
    Nd2 = _norm_cdf(d2)

    # Gamma (same for calls and puts)
    gamma = nd1 / (S * sigma * sqrt_T)

    # Vega (same for calls and puts, per 1% IV change)
    vega = S * nd1 * sqrt_T / 100.0

    if option_type.upper() == "CALL":
        delta = Nd1
        theta = (
            -(S * nd1 * sigma) / (2 * sqrt_T)
            - r * K * math.exp(-r * T) * Nd2
        ) / 365.0  # Per day
    else:
        delta = Nd1 - 1.0
        theta = (
            -(S * nd1 * sigma) / (2 * sqrt_T)
            + r * K * math.exp(-r * T) * _norm_cdf(-d2)
        ) / 365.0  # Per day

    return OptionGreeks(delta=delta, gamma=gamma, theta=theta, vega=vega)


def quote_option(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "CALL",
    spread_penalty: float = 0.03,
) -> OptionQuote:
    """Full quote: premium, greeks, intrinsic/time value breakdown.

    spread_penalty: percentage added to buy price / subtracted from sell price
    to simulate bid-ask spread (default 3%).
    """
    premium = price_option(S, K, T, r, sigma, option_type)
    # Apply spread penalty (buyer pays more, seller receives less)
    premium_with_spread = premium * (1.0 + spread_penalty)

    g = greeks(S, K, T, r, sigma, option_type)

    if option_type.upper() == "CALL":
        intrinsic = max(0.0, S - K)
    else:
        intrinsic = max(0.0, K - S)

    time_value = max(0.0, premium - intrinsic)

    return OptionQuote(
        premium=round(premium_with_spread, 2),
        greeks=g,
        intrinsic=round(intrinsic, 2),
        time_value=round(time_value, 2),
    )


def select_strike(
    price: float,
    strategy: str = "ATM",
    option_type: str = "CALL",
) -> float:
    """Select a strike price based on strategy.

    Args:
        price: Current underlying price
        strategy: "ATM", "5_OTM", "10_OTM", "5_ITM", "10_ITM"
        option_type: "CALL" or "PUT"

    Returns rounded strike price.
    """
    if strategy == "ATM":
        return _round_strike(price)

    pct = 0.0
    if "5" in strategy:
        pct = 0.05
    elif "10" in strategy:
        pct = 0.10
    elif "15" in strategy:
        pct = 0.15

    is_otm = "OTM" in strategy.upper()

    if option_type.upper() == "CALL":
        # OTM call = strike above current price
        if is_otm:
            return _round_strike(price * (1 + pct))
        return _round_strike(price * (1 - pct))
    else:
        # OTM put = strike below current price
        if is_otm:
            return _round_strike(price * (1 - pct))
        return _round_strike(price * (1 + pct))


def time_to_expiry_years(entry_date: str, expiry_date: str) -> float:
    """Convert dates to time-to-expiry in years."""
    entry = datetime.strptime(entry_date, "%Y-%m-%d")
    expiry = datetime.strptime(expiry_date, "%Y-%m-%d")
    days = (expiry - entry).days
    return max(0.0, days / 365.0)


def expiry_date_from_months(from_date: str, months: int) -> str:
    """Generate an expiry date N months from a given date.

    Returns the third Friday of the expiry month (standard options expiry).
    """
    dt = datetime.strptime(from_date, "%Y-%m-%d")
    # Add months
    month = dt.month + months
    year = dt.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    # Third Friday of that month
    import calendar
    cal = calendar.monthcalendar(year, month)
    # Find the third Friday (weekday 4)
    fridays = [week[4] for week in cal if week[4] != 0]
    third_friday = fridays[2] if len(fridays) >= 3 else fridays[-1]
    return f"{year}-{month:02d}-{third_friday:02d}"


def _round_strike(price: float) -> float:
    """Round to standard strike price increments."""
    if price < 25:
        return round(price * 2) / 2  # $0.50 increments
    elif price < 200:
        return round(price)  # $1 increments
    else:
        return round(price / 5) * 5  # $5 increments


# Default risk-free rate (approximate US Treasury yield)
DEFAULT_RISK_FREE_RATE = 0.045
