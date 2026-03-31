from __future__ import annotations

from datetime import datetime

import pandas as pd
from alpaca.data.enums import Adjustment
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestBarRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass

from src.config import get_alpaca_keys


class MarketData:
    def __init__(self, api_key: str | None = None, secret_key: str | None = None):
        if api_key and secret_key:
            self._api_key = api_key
            self._secret_key = secret_key
        else:
            self._api_key, self._secret_key = get_alpaca_keys()

        self._trading_client = TradingClient(
            self._api_key, self._secret_key, paper=True
        )
        self._data_client = StockHistoricalDataClient(
            self._api_key, self._secret_key
        )

    def get_account(self) -> dict:
        account = self._trading_client.get_account()
        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "portfolio_value": float(account.portfolio_value),
            "currency": account.currency,
        }

    def get_positions(self) -> list[dict]:
        positions = self._trading_client.get_all_positions()
        return [
            {
                "ticker": p.symbol,
                "qty": int(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pnl": float(p.unrealized_pl),
                "unrealized_pnl_pct": float(p.unrealized_plpc),
            }
            for p in positions
        ]

    def get_position(self, ticker: str) -> dict | None:
        try:
            p = self._trading_client.get_open_position(ticker)
            return {
                "ticker": p.symbol,
                "qty": int(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pnl": float(p.unrealized_pl),
                "unrealized_pnl_pct": float(p.unrealized_plpc),
            }
        except Exception:
            return None

    def get_bars(
        self,
        ticker: str,
        timeframe: TimeFrame = TimeFrame.Day,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        params = {
            "symbol_or_symbols": ticker,
            "timeframe": timeframe,
            "limit": limit,
            "adjustment": Adjustment.SPLIT,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        request = StockBarsRequest(**params)
        bars = self._data_client.get_stock_bars(request)
        df = bars.df

        if isinstance(df.index, pd.MultiIndex):
            df = df.droplevel("symbol")

        return df

    def get_latest_price(self, ticker: str) -> float | None:
        try:
            request = StockLatestBarRequest(symbol_or_symbols=ticker)
            bars = self._data_client.get_stock_latest_bar(request)
            bar = bars.get(ticker)
            return float(bar.close) if bar else None
        except Exception:
            return None

    def get_latest_prices(self, tickers: list[str]) -> dict[str, float]:
        try:
            request = StockLatestBarRequest(symbol_or_symbols=tickers)
            bars = self._data_client.get_stock_latest_bar(request)
            return {
                ticker: float(bar.close)
                for ticker, bar in bars.items()
                if bar is not None
            }
        except Exception:
            return {}

    def is_market_open(self) -> bool:
        clock = self._trading_client.get_clock()
        return clock.is_open
