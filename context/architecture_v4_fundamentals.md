# Architecture V4 — Fundamental Data Integration

## Status: IMPLEMENTED (2026-03-15)

## Problem

Claude makes investment decisions based on news, technicals, and thesis narrative only. It has no visibility into:
- Whether a company is profitable
- How expensive a stock is relative to earnings
- Balance sheet health (debt, cash flow)
- Revenue growth trajectory

This leads to picking stocks with great narratives but poor fundamentals (e.g. unprofitable growth stocks, crypto-adjacent plays with no earnings floor).

## What Was Added

### Data Source: yfinance
- Free, no API key required, good coverage across all universe tickers
- Provides ~5 quarters of historical quarterly financial statements (income + balance sheet)
- Current snapshot ratios via `.info` endpoint (P/E, margins, D/E, EV/EBITDA, short interest, insider %)
- Limitation: historical data only goes back ~15 months from today (mid-2024 as of March 2026)
- Future: FMP API key available in `.env` for deeper historical data if needed

### Tier 1 — Basic Valuation (implemented)
- **P/E ratio** — is the stock cheap or expensive vs earnings?
- **Revenue growth %** (quarter-over-quarter) — is the business actually growing?
- **Profit margin %** — is the company making money?
- **Debt/Equity ratio** — how leveraged is the balance sheet?
- **Profitability flag** — is net income positive?

### Tier 3 — Advanced (implemented)
- **EV/EBITDA** — better than P/E for comparing across capital structures
- **Short interest %** — crowdedness of short trades
- **Insider holding %** — management skin in the game

### Not Implemented (skipped)
- **PEG ratio** — redundant since Claude sees both P/E and growth separately
- **Free cash flow yield** — available via yfinance .info but not in historical statements
- **ROE** — available via .info but not prioritised for V4
- **Dividend yield** — Claude can infer from context

## Architecture

### New Files
- `src/research/fundamentals.py` — FundamentalsClient, FundamentalsCache, prompt formatting

### Modified Files
- `src/strategy/decision_engine.py` — FUNDAMENTALS section added to Claude's prompt
- `src/strategy/risk_v3.py` — Profitability gate on "highest" confidence tier
- `src/simulation/thesis_sim.py` — Pre-fetches fundamentals, passes to decision engine, checks profitability at execution
- `config/default.yaml` — `fundamentals` config section
- `src/config.py` — Replaced `get_tiingo_key()` with `get_fmp_key()`
- `requirements.txt` — Added yfinance dependency

### Data Flow

```
Sim Start
  → FundamentalsClient.prefetch_universe(77 tickers)
  → Cached as JSON in data/<sim_dir>/fundamentals_cache/

Each Review
  → build_fundamentals_prompt_section(tickers, as_of=review_date)
  → Point-in-time lookup: returns most recent quarter BEFORE review date
  → Added to Claude's prompt as FUNDAMENTALS section

New Position Execution
  → FundamentalsClient.is_profitable(ticker, as_of)
  → Passed to RiskManagerV3.evaluate_new_position(is_profitable=...)
  → If unprofitable + "highest" confidence → capped at "high" (10% max)
```

### Prompt Format (one line per ticker)
```
NVDA | P/E=65.2 | RevGr=+14.3% | Margin=26.1% | D/E=0.41 | EV/EBITDA=45.2 | Short=1.2% | Insider=0.1% | Profitable
T | P/E=8.3 | RevGr=-2.1% | Margin=12.4% | D/E=1.12 | EV/EBITDA=7.8 | Short=1.5% | Insider=0.3% | Profitable
COIN | P/E=N/A | Margin=-5.2% | D/E=2.31 | UNPROFITABLE
```

### Gating Rule (Option A)
- Unprofitable companies (negative net income) are **hard-capped** at "high" confidence (max 10% allocation)
- They cannot receive "highest" confidence (15%), regardless of what Claude requests
- This is enforced in `RiskManagerV3.evaluate_new_position()`, not in the prompt
- Claude is informed of this rule in the prompt so it can factor it into decisions

## Caching Strategy

- **Per-ticker JSON files** in `data/<sim_dir>/fundamentals_cache/<TICKER>.json`
- Fetched once at sim start via `prefetch_universe()`
- On-demand fetch for discovered tickers (outside the universe)
- Cache is per-sim-run directory (isolated between runs)
- Point-in-time lookups prevent future-knowledge leakage

## Backtesting Limitations

- yfinance provides ~5 quarters of historical quarterly statements
- As of March 2026, this covers approximately Q1 2025 → Q1 2026
- Sims before Q1 2025 will have limited or no fundamental data
- FMP API key is configured for future upgrade (needs paid plan for >5 quarters)
- For older backtests: fundamentals section shows "(No fundamental data available)"

## Key Design Decisions

- Fundamentals **inform Claude + gate risk**, they don't auto-trade
- Only watchlist + holdings get fundamentals in the prompt (not full universe every time)
- Discovered tickers get fundamentals fetched on-demand before execution
- Banks (JPM, GS) may not have EBITDA — handled gracefully with N/A
- Quarterly data is static between earnings — this is fine for thesis validation
- Prompt addition is compact: one line per ticker, key metrics only
