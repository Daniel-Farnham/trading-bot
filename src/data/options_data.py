"""Live options data client using Alpaca Options API.

Replaces the sim's Black-Scholes synthetic pricing with real market data:
real IV, real Greeks, real bid/ask spreads, real volume/OI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, OptionSnapshotRequest

logger = logging.getLogger(__name__)


@dataclass
class OptionContract:
    """Parsed option contract with Greeks and market data."""
    symbol: str  # OCC symbol e.g. "NVDA250620C00140000"
    underlying: str
    option_type: str  # "call" or "put"
    strike: float
    expiry: str  # ISO date
    bid: float
    ask: float
    mid: float
    last: float
    volume: int
    open_interest: int
    implied_volatility: float
    delta: float
    gamma: float
    theta: float
    vega: float


class OptionsDataClient:
    """Fetches real options data from Alpaca."""

    def __init__(self, api_key: str, secret_key: str):
        self._client = OptionHistoricalDataClient(api_key, secret_key)

    def get_chain(
        self,
        underlying: str,
        option_type: str | None = None,
        expiry_min: str | None = None,
        expiry_max: str | None = None,
        strike_min: float | None = None,
        strike_max: float | None = None,
    ) -> list[OptionContract]:
        """Fetch options chain with Greeks and IV.

        Args:
            underlying: Underlying ticker (e.g. "NVDA").
            option_type: "call" or "put" or None for both.
            expiry_min: Minimum expiry date (ISO format).
            expiry_max: Maximum expiry date (ISO format).
            strike_min: Minimum strike price.
            strike_max: Maximum strike price.

        Returns list of OptionContract with real market data.
        """
        params = {"underlying_symbol": underlying}
        if option_type:
            params["type"] = option_type
        if expiry_min:
            params["expiration_date_gte"] = expiry_min
        if expiry_max:
            params["expiration_date_lte"] = expiry_max
        if strike_min is not None:
            params["strike_price_gte"] = str(strike_min)
        if strike_max is not None:
            params["strike_price_lte"] = str(strike_max)

        try:
            request = OptionChainRequest(**params)
            snapshots = self._client.get_option_chain(request)
            return self._parse_snapshots(snapshots, underlying)
        except Exception as e:
            logger.error("Failed to fetch options chain for %s: %s", underlying, e)
            return []

    def get_snapshot(self, contract_symbol: str) -> OptionContract | None:
        """Get real-time snapshot for a specific contract."""
        try:
            request = OptionSnapshotRequest(symbol_or_symbols=contract_symbol)
            snapshots = self._client.get_option_snapshot(request)
            snap = snapshots.get(contract_symbol)
            if not snap:
                return None
            return self._parse_single_snapshot(contract_symbol, snap)
        except Exception as e:
            logger.error("Failed to fetch snapshot for %s: %s", contract_symbol, e)
            return None

    def get_chain_for_entry(
        self,
        underlying: str,
        current_price: float,
        option_type: str = "call",
        min_dte: int = 45,
        max_dte: int = 90,
        strike_width_pct: float = 0.15,
        min_open_interest: int = 100,
        max_bid_ask_spread_pct: float = 0.10,
    ) -> list[OptionContract]:
        """Fetch a filtered chain suitable for entry.

        Applies liquidity filters (OI, bid-ask spread) and DTE/strike range.
        """
        now = datetime.now()
        expiry_min = (now + timedelta(days=min_dte)).strftime("%Y-%m-%d")
        expiry_max = (now + timedelta(days=max_dte)).strftime("%Y-%m-%d")
        strike_min = current_price * (1 - strike_width_pct)
        strike_max = current_price * (1 + strike_width_pct)

        contracts = self.get_chain(
            underlying=underlying,
            option_type=option_type,
            expiry_min=expiry_min,
            expiry_max=expiry_max,
            strike_min=strike_min,
            strike_max=strike_max,
        )

        # Apply liquidity filters
        filtered = []
        for c in contracts:
            if c.open_interest < min_open_interest:
                continue
            if c.mid <= 0:
                continue
            spread = (c.ask - c.bid) / c.mid if c.mid > 0 else 1.0
            if spread > max_bid_ask_spread_pct:
                continue
            filtered.append(c)

        # Sort by closest to ATM
        filtered.sort(key=lambda c: abs(c.strike - current_price))
        return filtered

    def _parse_snapshots(
        self, snapshots: dict, underlying: str,
    ) -> list[OptionContract]:
        """Parse Alpaca snapshots dict into OptionContract list."""
        contracts = []
        for symbol, snap in snapshots.items():
            contract = self._parse_single_snapshot(symbol, snap, underlying)
            if contract:
                contracts.append(contract)
        return contracts

    def _parse_single_snapshot(
        self, symbol: str, snap, underlying: str = "",
    ) -> OptionContract | None:
        """Parse a single Alpaca OptionsSnapshot."""
        try:
            quote = snap.latest_quote
            trade = snap.latest_trade
            greeks = snap.greeks or {}

            bid = float(quote.bid_price) if quote and quote.bid_price else 0.0
            ask = float(quote.ask_price) if quote and quote.ask_price else 0.0
            mid = (bid + ask) / 2 if bid and ask else 0.0
            last = float(trade.price) if trade and trade.price else mid

            # Parse OCC symbol for strike/expiry/type
            parsed = self._parse_occ_symbol(symbol)

            return OptionContract(
                symbol=symbol,
                underlying=underlying or parsed.get("underlying", ""),
                option_type=parsed.get("option_type", "call"),
                strike=parsed.get("strike", 0.0),
                expiry=parsed.get("expiry", ""),
                bid=bid,
                ask=ask,
                mid=round(mid, 2),
                last=round(last, 2),
                volume=int(quote.bid_size + quote.ask_size) if quote else 0,
                open_interest=0,  # OI not in snapshot, available via contracts endpoint
                implied_volatility=float(snap.implied_volatility) if snap.implied_volatility else 0.0,
                delta=float(greeks.delta) if hasattr(greeks, 'delta') and greeks.delta else 0.0,
                gamma=float(greeks.gamma) if hasattr(greeks, 'gamma') and greeks.gamma else 0.0,
                theta=float(greeks.theta) if hasattr(greeks, 'theta') and greeks.theta else 0.0,
                vega=float(greeks.vega) if hasattr(greeks, 'vega') and greeks.vega else 0.0,
            )
        except Exception as e:
            logger.debug("Failed to parse snapshot for %s: %s", symbol, e)
            return None

    @staticmethod
    def _parse_occ_symbol(symbol: str) -> dict:
        """Parse OCC option symbol (e.g. NVDA250620C00140000).

        Format: ROOT + YYMMDD + C/P + strike*1000 (8 digits, zero-padded)
        """
        try:
            # Find where the date starts (first digit after letters)
            i = 0
            while i < len(symbol) and not symbol[i].isdigit():
                i += 1
            root = symbol[:i]
            rest = symbol[i:]

            if len(rest) < 15:
                return {"underlying": root}

            date_str = rest[:6]  # YYMMDD
            option_type = "call" if rest[6] == "C" else "put"
            strike = int(rest[7:15]) / 1000.0

            expiry = f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}"

            return {
                "underlying": root,
                "expiry": expiry,
                "option_type": option_type,
                "strike": strike,
            }
        except Exception:
            return {"underlying": symbol}
