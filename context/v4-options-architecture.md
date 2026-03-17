# V4 Architecture — Hybrid Shares + Options Trading

## Overview

V4 extends the V3 thesis-driven strategy by giving Claude a choice of instrument: **shares or options**. The decision engine, thesis system, and memory all stay the same. Claude decides per-position whether to use shares (default) or options (when conviction is high enough to warrant leveraged exposure).

**Why hybrid:**
- Shares remain the workhorse — reliable, no time decay, can hold indefinitely
- Options are a precision tool for high-conviction moments — capped downside, leveraged upside
- Claude decides when the setup justifies the extra risk/reward of options
- Avoids forcing options on low-conviction or uncertain trades

## Instrument Selection — When Options vs Shares

Claude chooses the instrument per position. The decision is based on a **conviction composite** — not a single signal, but the alignment of multiple factors the bot already tracks:

### Signals available today
| Signal | Source | Options signal |
|--------|--------|---------------|
| Confidence level | Claude's JSON response (`high/medium/low`) | `high` required |
| Theme score | themes.md (1-5) | >= 4 for the relevant theme |
| Technical alignment | RSI + SMA50 + MACD + Bollinger Bands | Multiple technicals confirming direction |
| Thesis status | active_theses.md | `STRENGTHENING` preferred |

### Signals to add
| Signal | How to compute | Options signal |
|--------|---------------|---------------|
| News intensity | Count of headlines per ticker in research window | 5+ relevant headlines |
| Cross-thesis alignment | Multiple theses pointing same direction | 2+ theses supporting the trade |

### Decision rule

Claude decides per-position, but with a **minimum threshold**:
- `confidence` must be `"high"` to use options — this is a hard gate
- Beyond that, Claude weighs theme score, technicals, thesis momentum, and news intensity
- Claude must explain WHY options are appropriate in the thesis text

**Prompt guidance (not rigid rules):**
- High confidence + strong theme + technical confirmation → options candidate
- Medium/low confidence, or uncertain regime → shares
- Bear market hedging via puts → options make sense even at medium confidence (capped loss)

## Execution Flow

```
Claude Decision                         Execution
────────────────────────────────────────────────────────────────
BUY LONG NVDA 6% (SHARES)          →   Buy 25 shares @ $240
BUY LONG NVDA 6% (OPTIONS)         →   Buy 1-2 calls, 60-90 DTE, ATM
SHORT T 5% (SHARES)                →   Short 175 shares @ $26
SHORT T 5% (OPTIONS)               →   Buy 1-2 puts, 60-90 DTE, ATM
CLOSE NVDA (SHARES)                →   Sell 25 shares
CLOSE NVDA (OPTIONS)               →   Sell to close the call(s)
```

## JSON Schema Changes

The `new_positions` schema adds an `instrument` field:

```json
{
  "ticker": "NVDA",
  "action": "BUY",
  "allocation_pct": 6,
  "direction": "LONG",
  "instrument": "OPTIONS",
  "thesis": "Full thesis explaining why we're buying AND why options are justified",
  "invalidation": "What would make us sell",
  "target_price": 300.0,
  "stop_price": 200.0,
  "horizon": "3-6 months",
  "confidence": "high",
  "timing_note": "RSI=28, theme score 5/5, 3 bullish catalysts this week"
}
```

- `instrument` defaults to `"SHARES"` if omitted — backwards compatible with V3
- `stop_price` is still included for shares positions; ignored for options (max loss = premium)
- Options positions MUST have `confidence: "high"`

## Key Design Decisions

### 1. Buy-only options (no selling)
- LONG thesis → buy calls
- SHORT thesis → buy puts
- Never sell naked options — unlimited risk doesn't fit the strategy
- Level 2 options approval is sufficient for live trading

### 2. Strike selection
- **ATM or 1 strike OTM** — balance between premium cost and delta
- ATM gives ~0.50 delta (good directional exposure)
- 1 strike OTM is cheaper but needs a bigger move to profit
- Strike preference based on confidence:
  - Very strong setup → ATM (higher premium, higher delta)
  - Strong but not extreme → 1 strike OTM (cheaper, asymmetric payoff)

### 3. DTE selection
- **60-90 DTE default** — matches thesis hold periods (weeks to quarters)
- Theta decay is manageable at 60+ DTE (~0.5-1% per day)
- Roll or close at 21 DTE if thesis still active (avoid accelerating theta)

### 4. Position sizing
- **Shares:** sized by notional value (e.g. 6% allocation = $6,000 in shares)
- **Options:** sized by premium cost (e.g. 6% allocation = $6,000 in premium)
- Options premium sizing naturally caps max loss per position
- A single options position should never exceed 8% of portfolio in premium

### 5. Stops and exits
- **Shares:** keep existing 18% catastrophic stops
- **Options:** no stops needed — max loss is the premium paid
- Both: thesis-driven exits remain the primary exit mechanism
- Options-specific: close or roll at 21 DTE remaining

## New Technicals for V4

V3 technicals (RSI, MACD, SMA50, Bollinger Bands) are good for directional calls but miss the **volatility dimension** that drives option pricing and improves share trading decisions. These additions help both instruments.

### Add to `src/analysis/technical.py`

#### 1. Historical Volatility (HV20)
- Annualized standard deviation of daily log returns over 20 days
- **Options:** tells you if the stock has been volatile — high HV = expensive options, low HV = cheap options
- **Shares:** high HV stocks warrant smaller position sizes; low HV before a catalyst = potential breakout setup
- Calculation: `std(log_returns, 20) * sqrt(252) * 100`

#### 2. HV Percentile (HV rank over 1 year)
- Where is current HV relative to the last 252 trading days? Expressed as 0-100
- **Options:** HV in 20th percentile = options are historically cheap = good time to buy calls/puts. HV in 80th percentile = expensive, prefer shares
- **Shares:** HV at extremes signals regime change — very low HV often precedes big moves (calm before the storm), very high HV signals peak fear (potential capitulation entry)
- This is the single most important metric for the shares-vs-options instrument decision

#### 3. ATR% (ATR as percentage of price)
- Already have ATR — just express it as `(ATR / price) * 100`
- **Options:** helps estimate expected move over the option's life — a 5% ATR stock needs ATM strikes, a 1% ATR stock can go slightly OTM
- **Shares:** enables dynamic stop placement instead of the fixed 18% catastrophic stop. A low-volatility stock (ATR% = 1.5%) doesn't need an 18% stop; a high-volatility stock (ATR% = 5%) might need wider. Rule of thumb: catastrophic stop = 3-4x ATR%
- Makes stops comparable across the universe — $5 ATR on a $50 stock (10%) is very different from $5 on a $500 stock (1%)

#### 4. ADX (Average Directional Index)
- Measures trend **strength** (0-100), not direction. ADX > 25 = strong trend, ADX < 20 = weak/choppy
- **Options:** strong trend (ADX > 30) + directional bet = options amplify the move. Weak trend (ADX < 15) = time decay eats you alive waiting for the move
- **Shares:** directly addresses the falling knife problem from bull-to-bear sims. "RSI=35 with ADX=40" means strong downtrend — don't catch it. "RSI=35 with ADX=12" means choppy, oversold, good contrarian entry. Claude learned this the hard way in Lesson 21 (META falling knife) — ADX would have flagged it

#### 5. OBV Trend (On-Balance Volume direction)
- Cumulative volume indicator — OBV rises on up days, falls on down days. Track the 20-day slope direction (rising/falling/flat)
- **Options:** OBV divergence from price (price up, OBV flat/down) signals a move that lacks conviction — don't pay premium for it
- **Shares:** confirms whether moves are real. A breakout on declining OBV is suspect. A selloff on low OBV is less concerning. Particularly useful for the bot's re-entry decisions after stop-outs — was the selloff high-volume capitulation (good re-entry) or low-volume drift (more downside likely)?

### How Claude sees these in the prompt

Current format:
```
NVDA: $240.50 | RSI=35 | below SMA50 | MACD bearish | near lower BB
```

V4 format:
```
NVDA: $240.50 | RSI=35 | below SMA50 | MACD bearish | near lower BB | HV=42% (25th pctl) | ATR%=3.2% | ADX=38 | OBV falling
```

The HV percentile is the key signal for instrument selection:
- `HV 25th pctl` → "options are cheap" → Claude considers OPTIONS if conviction is high
- `HV 75th pctl` → "options are expensive" → Claude defaults to SHARES

### What NOT to add to technical.py (get from Alpaca live data instead)

- **Implied Volatility (IV)** — can't calculate from historical prices. Comes from the option chain via `OptionHistoricalDataClient` at execution time. Belongs in the options broker, not technical.py.
- **Greeks (delta, gamma, theta, vega)** — same, live data from option chain API.
- **IV Rank / IV Percentile** — requires historical IV data that Alpaca may not provide. HV Percentile is the backtestable proxy.

## Alpaca SDK Support

The `alpaca-py` SDK (v0.43.2, already installed) fully supports options:

### Relevant SDK components

```python
# Trading
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, GetOptionContractsRequest
from alpaca.trading.enums import (
    OrderSide, TimeInForce, PositionIntent,
    ContractType, AssetClass
)

# Data
from alpaca.data import OptionHistoricalDataClient, OptionChainRequest
```

### Placing an options order

```python
# Option symbol format: "NVDA250418C00240000" (NVDA Apr 18 2025 $240 Call)
order = LimitOrderRequest(
    symbol="NVDA250418C00240000",
    qty=1,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.DAY,
    limit_price=15.50,
    position_intent=PositionIntent.BUY_TO_OPEN,
)
result = client.submit_order(order)
```

### Getting option chains

```python
# Find available contracts
req = GetOptionContractsRequest(
    underlying_symbols=["NVDA"],
    type=ContractType.CALL,
    expiration_date_gte="2025-04-01",
    expiration_date_lte="2025-06-30",
    strike_price_gte="230",
    strike_price_lte="260",
)
contracts = client.get_option_contracts(req)

# Get live quotes with greeks
data_client = OptionHistoricalDataClient(api_key, secret_key)
chain = data_client.get_option_chain(OptionChainRequest(
    underlying_symbol="NVDA",
    expiration_date_gte="2025-04-01",
    expiration_date_lte="2025-06-30",
    strike_price_gte=230,
    strike_price_lte=260,
))
```

### Paper trading
Options are enabled by default on paper accounts — no approval needed.

## New Components Needed

### 1. Options broker (`src/execution/options_broker.py`)
- Wraps `TradingClient` + `OptionHistoricalDataClient`
- `select_contract(ticker, direction, dte_target)` → picks strike/expiry
- `open_position(contract_symbol, qty, limit_price)` → BUY_TO_OPEN
- `close_position(contract_symbol, qty)` → SELL_TO_CLOSE
- `get_positions()` → current options holdings
- `get_greeks(contract_symbol)` → delta, theta, IV for monitoring

### 2. Contract selector (`src/strategy/contract_selector.py`)
- Given: ticker, direction (LONG/SHORT), allocation_pct
- Returns: specific contract symbol, quantity, limit price
- Logic:
  1. Fetch option chain for the underlying
  2. Filter to 60-90 DTE expiries
  3. Select ATM or 1-strike-OTM
  4. Calculate quantity from allocation / premium cost
  5. Return the OCC symbol (e.g., `NVDA250418C00240000`)

### 3. Theta monitor (extension to review process)
- At each review, check DTE on all open options positions
- If DTE < 21 and thesis still active → roll to next expiry
- If DTE < 21 and thesis weakening → close, don't roll
- Report theta decay drag in portfolio summary

### 4. Execution router (`src/execution/router.py`)
- Reads `instrument` field from Claude's decision
- Routes to existing stock broker OR new options broker
- Single entry point for all trade execution

## Simulation Approach

**Historical options pricing is hard.** Alpaca provides real-time option chains but likely not historical options data for backtesting.

**Recommendation: Skip options backtesting, go straight to paper trading.**
- V3 share-based sims already validate thesis quality (direction + timing)
- V4 options execution is a mechanical translation — same thesis, different instrument
- Paper trading tests the real questions: contract selection, premium costs, liquidity, theta management
- Run options paper alongside shares paper for A/B comparison

## Migration Path

```
Phase 1: V3 paper trading (shares only)
  → Validate thesis engine works in real-time
  → Build confidence in live execution

Phase 2: V4 paper trading (shares + options)
  → Add options broker + contract selector + execution router
  → Claude chooses instrument per position
  → Compare options vs shares outcomes on same theses

Phase 3: V4 live trading (small account)
  → Small capital you're prepared to lose
  → Hybrid portfolio: shares default, options for high conviction
  → Level 2 options approval on Alpaca live account
```

## Risk Considerations

- **Theta decay** — 60-90 DTE options lose ~0.5-1% daily. A thesis that's "right but slow" still loses money on the options portion.
- **IV crush** — Buying before earnings means paying high IV. Post-event IV drop can kill profits even if direction is correct. Consider avoiding options entries within 2 weeks of known earnings dates.
- **Liquidity** — Not all stocks have liquid options. Stick to the universe tickers (large caps with tight bid-ask spreads). If spread > 10% of premium, use shares instead.
- **Win rate matters more for options** — Below ~55% win rate, options lose money after premiums. The hard gate on `confidence: "high"` filters for the best setups.
- **Total loss is real** — Unlike shares where you can hold through drawdowns, options can expire worthless. Every options position can go to zero. This is why premium sizing caps exposure.
- **Portfolio mix** — In practice, expect ~70-80% of positions to be shares, ~20-30% options. Options should be the exception, not the rule.
