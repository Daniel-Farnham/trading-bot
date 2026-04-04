"""Research tools for Call 1 discovery.

Defines Anthropic tool-use tools that Claude can call during Call 1 to
explore opportunities beyond pre-fetched news. Each tool wraps an existing
API client (Alpaca, yfinance, technicals).

Tools:
- search_news: Search Alpaca news by keyword, sector, or ticker
- get_fundamentals: Pull financial data for a ticker (P/E, revenue growth, margins)
- get_price_action: Recent price, 52-week range, volume
- get_technicals: RSI, MACD, OBV, ATR from live bars
- screen_by_theme: Find stocks related to a theme/sector keyword
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# --- Tool Definitions (Anthropic format) ---

RESEARCH_TOOLS = [
    {
        "name": "search_news",
        "description": (
            "Search financial news headlines by keyword, sector, or ticker symbol. "
            "Use this to investigate stories, find related companies, or discover "
            "stocks making moves that aren't in the pre-fetched headlines."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ticker symbols to search news for (e.g. ['NVDA', 'MU']). Optional.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of articles to return (default 10, max 30).",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_fundamentals",
        "description": (
            "Get fundamental financial data for a stock: P/E ratio, revenue growth, "
            "profit margins, debt/equity, EV/EBITDA, short interest. Use this to "
            "validate whether a stock has real fundamental strength behind a thesis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g. 'NVDA').",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_price_action",
        "description": (
            "Get recent price action for a stock: current price, 52-week high/low, "
            "recent performance (1 week, 1 month, 3 month returns), average volume. "
            "Use this to check if a stock is at a good entry point."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g. 'PLTR').",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_technicals",
        "description": (
            "Get technical indicators for a stock: RSI, MACD (bullish/bearish), "
            "SMA50 (above/below), OBV trend (rising/falling), ATR%, historical "
            "volatility percentile. Use this to check if technicals support an entry."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g. 'CEG').",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "screen_by_theme",
        "description": (
            "Screen for stocks related to a theme or sector. Returns a list of "
            "relevant tickers with brief descriptions. Use this to discover stocks "
            "you don't know about — e.g. screen_by_theme('data center cooling') "
            "might surface VRT, CARR, or smaller players."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "theme": {
                    "type": "string",
                    "description": "Theme or sector to screen for (e.g. 'nuclear energy', 'AI memory', 'data center cooling').",
                },
            },
            "required": ["theme"],
        },
    },
]


# --- Tool Execution ---


class ResearchToolExecutor:
    """Executes research tools using real API clients."""

    def __init__(self, news_client, market_data, technical_analyzer, fundamentals_client):
        self._news = news_client
        self._market = market_data
        self._technicals = technical_analyzer
        self._fundamentals = fundamentals_client

    def execute(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool and return JSON result string."""
        logger.info("TOOL CALL: %s(%s)", tool_name, json.dumps(tool_input)[:200])

        handlers = {
            "search_news": self._search_news,
            "get_fundamentals": self._get_fundamentals,
            "get_price_action": self._get_price_action,
            "get_technicals": self._get_technicals,
            "screen_by_theme": self._screen_by_theme,
        }

        handler = handlers.get(tool_name)
        if not handler:
            result = {"error": f"Unknown tool: {tool_name}"}
        else:
            try:
                result = handler(tool_input)
            except Exception as e:
                logger.error("Tool %s failed: %s", tool_name, e)
                result = {"error": f"Tool failed: {str(e)}"}

        result_str = json.dumps(result, default=str)
        logger.info("TOOL RESULT: %s → %s", tool_name, result_str[:500])
        return result_str

    def _search_news(self, params: dict) -> dict:
        symbols = params.get("symbols")
        limit = min(params.get("limit", 10), 30)

        yesterday = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")

        articles = self._news.get_news(
            symbols=symbols,
            start_date=yesterday,
            end_date=today,
            limit=limit,
        )

        return {
            "articles": [
                {
                    "title": a.get("title", ""),
                    "date": a.get("publishedDate", "")[:10],
                    "tickers": a.get("tickers", [])[:5],
                    "summary": a.get("description", "")[:200],
                }
                for a in articles
            ],
            "count": len(articles),
        }

    def _get_fundamentals(self, params: dict) -> dict:
        ticker = params["ticker"].upper()
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            info = stock.info

            return {
                "ticker": ticker,
                "name": info.get("longName", ticker),
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", "Unknown"),
                "market_cap": info.get("marketCap"),
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "revenue_growth_yoy": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "profit_margin": info.get("profitMargins"),
                "gross_margin": info.get("grossMargins"),
                "debt_to_equity": info.get("debtToEquity"),
                "ev_to_ebitda": info.get("enterpriseToEbitda"),
                "short_percent": info.get("shortPercentOfFloat"),
                "insider_percent": info.get("heldPercentInsiders"),
                "revenue_ttm": info.get("totalRevenue"),
                "free_cash_flow": info.get("freeCashflow"),
            }
        except Exception as e:
            return {"ticker": ticker, "error": str(e)}

    def _get_price_action(self, params: dict) -> dict:
        ticker = params["ticker"].upper()
        try:
            start = datetime.now() - timedelta(days=120)
            bars = self._market.get_bars(ticker, start=start, limit=90)
            if bars.empty:
                return {"ticker": ticker, "error": "No price data available"}

            current = float(bars.iloc[-1]["close"])
            high_52w = float(bars["high"].max())
            low_52w = float(bars["low"].min())

            # Returns
            returns = {}
            for label, days in [("1w", 5), ("1m", 21), ("3m", 63)]:
                if len(bars) > days:
                    past = float(bars.iloc[-(days + 1)]["close"])
                    returns[label] = round(((current - past) / past) * 100, 1)

            avg_volume = int(bars["volume"].mean()) if "volume" in bars.columns else 0

            return {
                "ticker": ticker,
                "current_price": round(current, 2),
                "high_52w": round(high_52w, 2),
                "low_52w": round(low_52w, 2),
                "pct_from_high": round(((current - high_52w) / high_52w) * 100, 1),
                "returns": returns,
                "avg_daily_volume": avg_volume,
            }
        except Exception as e:
            return {"ticker": ticker, "error": str(e)}

    def _get_technicals(self, params: dict) -> dict:
        ticker = params["ticker"].upper()
        try:
            start = datetime.now() - timedelta(days=120)
            bars = self._market.get_bars(ticker, start=start, limit=60)
            if bars.empty or len(bars) < 20:
                return {"ticker": ticker, "error": "Insufficient data"}

            snap = self._technicals.analyze(ticker, bars)

            result = {"ticker": ticker, "price": round(snap.close, 2)}
            if snap.rsi is not None:
                result["rsi"] = round(snap.rsi, 1)
            if snap.macd_signal is not None:
                result["macd"] = snap.macd_signal
            if snap.sma50 is not None:
                result["sma50"] = round(snap.sma50, 2)
                result["above_sma50"] = snap.close > snap.sma50
            if snap.obv_trend is not None:
                result["obv_trend"] = snap.obv_trend
            if snap.atr_pct is not None:
                result["atr_pct"] = round(snap.atr_pct, 1)
            if snap.hv_percentile is not None:
                result["hv_percentile"] = round(snap.hv_percentile, 0)
            if snap.adx is not None:
                result["adx"] = round(snap.adx, 1)

            return result
        except Exception as e:
            return {"ticker": ticker, "error": str(e)}

    def _screen_by_theme(self, params: dict) -> dict:
        """Screen for stocks by theme using yfinance sector/industry data.

        This is a simple screen — searches our universe first, then tries
        to find related stocks via yfinance industry classification.
        """
        theme = params["theme"].lower()
        try:
            import yfinance as yf

            # Theme keyword → industry/sector mappings
            theme_keywords = {
                "nuclear": ["Utilities—Regulated Electric", "Utilities—Independent Power Producers"],
                "solar": ["Solar"],
                "data center": ["REIT—Specialty", "REIT—Industrial", "Electrical Equipment & Parts"],
                "memory": ["Semiconductors", "Semiconductor Memory"],
                "semiconductor": ["Semiconductors", "Semiconductor Equipment & Materials"],
                "ai": ["Semiconductors", "Software—Infrastructure", "Information Technology Services"],
                "defense": ["Aerospace & Defense"],
                "biotech": ["Biotechnology"],
                "pharma": ["Drug Manufacturers—General", "Drug Manufacturers—Specialty & Generic"],
                "energy": ["Oil & Gas Integrated", "Oil & Gas E&P", "Oil & Gas Midstream"],
                "fintech": ["Software—Infrastructure", "Financial Data & Stock Exchanges"],
                "cloud": ["Software—Infrastructure", "Software—Application"],
                "cybersecurity": ["Software—Infrastructure"],
                "ev": ["Auto Manufacturers", "Auto Parts"],
                "mining": ["Other Industrial Metals & Mining", "Copper"],
                "cooling": ["Building Products & Equipment", "Specialty Industrial Machinery"],
            }

            # Find matching industries
            matching_industries = []
            for keyword, industries in theme_keywords.items():
                if keyword in theme:
                    matching_industries.extend(industries)

            if not matching_industries:
                return {
                    "theme": theme,
                    "stocks": [],
                    "note": f"No industry mapping for '{theme}'. Try: {', '.join(theme_keywords.keys())}",
                }

            # Screen a broad set of tickers for matching industries
            screen_tickers = [
                "NVDA", "AMD", "INTC", "MU", "AVGO", "TSM", "ARM", "SMCI", "MRVL", "QCOM",
                "MSFT", "GOOGL", "AMZN", "META", "CRM", "PLTR", "SNOW", "NET", "CRWD", "PANW",
                "LLY", "NVO", "MRNA", "REGN", "VRTX", "GILD", "ABBV", "NTRA", "INSM",
                "XOM", "CVX", "OXY", "NEE", "CEG", "VST", "ENPH", "FSLR", "LNG",
                "VRT", "EQIX", "DLR", "PWR", "EME", "ETN", "AMT", "PLD",
                "RTX", "LMT", "GE", "NOC", "HII", "LHX",
                "JPM", "GS", "V", "MA", "SQ", "COIN", "SOFI", "HOOD",
                "RIVN", "F", "GM", "TSLA", "LCID",
                "FCX", "NUE", "SCCO", "RIO", "BHP",
                "CARR", "JCI", "TT", "GNRC",
            ]

            results = []
            for ticker in screen_tickers:
                try:
                    stock = yf.Ticker(ticker)
                    info = stock.info
                    industry = info.get("industry", "")
                    if industry in matching_industries:
                        results.append({
                            "ticker": ticker,
                            "name": info.get("longName", ticker),
                            "industry": industry,
                            "market_cap": info.get("marketCap"),
                            "pe_ratio": info.get("trailingPE"),
                            "revenue_growth": info.get("revenueGrowth"),
                        })
                except Exception:
                    continue

                if len(results) >= 10:
                    break

            return {
                "theme": theme,
                "industries_searched": matching_industries,
                "stocks": results,
                "count": len(results),
            }
        except Exception as e:
            return {"theme": theme, "error": str(e)}
