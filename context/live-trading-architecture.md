# Live Trading Architecture

## Overview

Concentrated, Druckenmiller-style macro trading system using Alpaca paper/live API. Three scheduled calls per day plus a breaking news alert system. The strategy prioritises fewer, larger conviction bets over diversified small positions. Claude acts as CIO — identifying regime changes, sizing big on best ideas, and holding winners for months.

**Investment Philosophy:** Global macro with asymmetric risk management. We identify structural regime changes (policy shifts, sector rotations, technology disruptions), bet concentrated on the highest-conviction ideas, and use technical timing for entries. Scout positions test theses cheaply; core positions are the alpha engine.

**Target:** 30%+ annualized return, 10%+ alpha over S&P 500.

## Strategy: Two-Tier Position Management

The core innovation. Every position is either a **scout** or a **core** bet:

### Scout Positions (Probing the Market)
- **Confidence:** low / medium
- **Allocation:** 3-8% of portfolio
- **Stops:** Mechanical — Claude's stated stop price enforced daily by the system
- **Targets:** Mechanical — Claude's stated target price enforced daily
- **Purpose:** Test a thesis cheaply. "I think tariffs will crush NKE — let me put 5% on it and see."
- **Holding period:** Days to weeks
- **If stopped out:** Moves to WATCHING (thesis preserved, 6-review expiry for re-entry)

### Core Positions (Going for the Jugular)
- **Confidence:** high / highest
- **Allocation:** Uncapped — Claude decides the size. 10%, 20%, 40% — size to conviction.
- **Stops:** NO mechanical stop. Only a 30% catastrophic safety net for black swan protection.
- **Targets:** NO mechanical target. Claude decides when to take profits at each review.
- **Purpose:** The alpha engine. These are the Druckenmiller bets — big, concentrated, held through volatility.
- **Holding period:** Weeks to months
- **Exit criteria:** Thesis invalidation only. A 10% drawdown on a core position whose thesis is intact is NORMAL and should be held. Triple distribution (below SMA50 + MACD bearish + OBV falling) is a strong thesis-invalidation signal.
- **If stopped at 30% catastrophic:** Something has gone very wrong. Review what happened.

### Pyramiding (Adding to Winners)

When a core position is working — thesis strengthening, OBV rising, catalyst confirmed — Claude can add to it rather than opening new positions. This is how Druckenmiller built his biggest wins.

**How it works:**
- Claude re-submits an existing ticker in `new_positions` with the **total** target allocation (not the additional amount)
- System calculates: target allocation minus current allocation = additional shares needed
- Buys additional shares at current market price
- Recalculates weighted average entry price across all purchases
- Respects the 20% cash reserve

**Example:**
- NVDA currently held at 10% ($10k, 80 shares @ $125)
- Claude submits: `{"ticker": "NVDA", "allocation_pct": 25, "confidence": "high"}`
- System buys ~100 additional shares at current price $155
- Result: 180 shares, avg entry $142.50, ~25% allocation

**Rules:**
- Only pyramid into winning positions with strengthening thesis
- Never average down into losers — that's the opposite of pyramiding
- System only triggers if additional allocation is >2% (prevents noise)
- Your biggest winners should become your biggest positions

### Scout → Core Upgrade Path

Scouts can be upgraded to core by re-submitting with higher confidence:
1. Scout confirms thesis (+5-10% gain, OBV rising, news validation)
2. Claude re-submits with "high" or "highest" confidence
3. System automatically widens stop from mechanical to 30% catastrophic
4. System removes mechanical target (lets position run)
5. If Claude also requests higher allocation, shares are added (pyramid)

This is the full lifecycle: **Scout → Confirm → Upgrade → Pyramid → Hold → Exit on thesis break**

### Position Sizing

| Confidence | Allocation | Stop Behavior | Tier | Requirement |
|-----------|-----------|---------------|------|-------------|
| low | Max 5% | Mechanical (Claude's stop) | Scout | Any thesis |
| medium | Max 8% | Mechanical (Claude's stop) | Scout | Thesis + 1 technical signal |
| high | Uncapped (Claude decides) | 30% catastrophic only | Core | Thesis + multiple technicals aligned |
| highest | Uncapped (Claude decides) | 30% catastrophic only | Core | Thesis + technicals + fundamentals + macro regime all aligned. Requires profitable company. |

### Portfolio Constraints
- Max 8 positions (prefer 5-6 concentrated bets)
- Min 20% cash at all times
- Max 30% total short exposure
- Max 15% single short position
- Max drawdown circuit breaker: 30%
- Profitability gate: unprofitable companies capped at "high" confidence tier

## Core Features (Validated in Sims)

Proven through Q4 2024 — Q4 2025 backtesting across bull, correction, tariff shock, and recovery regimes:

- **Two-tier position management** — Scout positions (mechanical stops/targets, capped at 5-8%) vs core positions (thesis-based exits, uncapped allocation, 30% catastrophic safety net). Core positions are the alpha engine.
- **Pyramiding into winners** — Claude can add to existing core positions by re-submitting with a higher target allocation. System calculates additional shares needed, buys at current price, recalculates weighted average entry. Biggest winners should become biggest positions.
- **Scout → Core upgrade path** — Scouts that confirm the thesis can be upgraded to core. System automatically widens stops, removes mechanical targets, and optionally adds shares in a single action.
- **Thesis watching lifecycle** — Stopped-out scout positions move to WATCHING status (compressed 1-liner). Claude can re-enter with context of the prior attempt. Auto-expire after 6 reviews. Claude closing a position = thesis invalidated = deleted.
- **Seed beliefs (cross-regime)** — After each sim/live period, a belief consolidator merges lessons into durable principles. Only beliefs validated across 2+ market regimes survive. Max 5 seed beliefs in `data/seed_beliefs.md`. Loaded for live trading, never during backtests.
- **Fundamentals integration** — yfinance: P/E, revenue growth, margins, D/E, EV/EBITDA, short interest %, insider %. Cached as JSON. 45-day reporting lag for backtest integrity. Profitability gate: unprofitable companies cannot reach "highest" confidence.
- **In-run beliefs** — Max 5 long-term principles, consolidated from lessons during monthly reviews
- **Scored lessons** — Max 15, scored 1-5, evidence-based increment/decrement, auto-evict lowest at cap
- **Theme discovery** — Claude discovers themes from news, scored 1-5, removed below 1
- **Anti-churning** — Trade count tracked, Claude warned when over-trading. But also nudged to deploy when sitting on excessive cash.

## Daily Flow

### Call 1: News & Macro Scan (Pre-market / First Hour)

**Purpose:** What happened? What matters for our portfolio and themes?

**Scope:** Scans ALL news — not limited to the 77-stock universe. If a stock outside our universe is making headlines, Call 1 flags it for screening in Call 2.

**Inputs:**
- Alpaca News API — overnight and morning headlines (broad market, not filtered to universe)
- Current themes and their scores
- Current holdings list (tickers only, not full portfolio)

**Outputs:**
- Macro assessment (1 paragraph)
- Theme impact — any themes strengthening/weakening?
- Flagged tickers (IN universe) — existing watchlist/universe stocks with relevant news
- Flagged tickers (NEW) — stocks outside our universe getting significant coverage
- Emerging theme signals — news patterns that don't fit existing themes

**Cost:** Low — short prompt, news context only, no technicals or fundamentals

### Call 2: Technical & Fundamental Screen (Mid-day)

**Purpose:** Which stocks have actionable setups right now?

**Scope:** Screens three pools:
1. **Holdings** (~5-8) — always screened, full technicals + fundamentals
2. **Watchlist** (~10-15) — always screened, full technicals + fundamentals
3. **Call 1 flagged tickers** (~3-5) — screened on-demand, including stocks OUTSIDE the universe

For new tickers outside the universe, Call 2 automatically:
- Downloads price history (on-demand bar fetch)
- Fetches fundamentals via yfinance
- Computes full technicals (RSI, MACD, SMA50, OBV, ADX, HV, ATR%)

**Outputs:**
- Entry candidates with suggested tier (scout vs core)
- Exit alerts — core holdings showing thesis deterioration
- Short candidates — stocks with bearish setup in declining themes
- Watchlist updates
- Proposed actions shortlist — 3-5 most actionable ideas

**Cost:** Medium — scales with watchlist size, not universe size.

### Call 2.5: Breaking News Alert (Every 30-60 mins between Call 2 and Call 3)

**Purpose:** Catch dramatic events requiring immediate action.

**Trigger conditions** (any of these):
- Multiple sources reporting same high-impact event
- Market-moving keywords: war, sanctions, Fed emergency, default, pandemic, policy reversal
- VIX spike above threshold
- Individual holding down/up >8% intraday (higher threshold for concentrated portfolio)

**If triggered:**
- Emergency prompt with portfolio context
- Can execute immediate trades (close positions, hedge)
- Replaces scheduled Call 3

**Cost:** Very low — lightweight poll, only escalates on genuine alerts

### Call 3: Decision & Execution (Late Afternoon / After Close)

**Purpose:** Make buy/sell/hold decisions. The main decision-making call.

**Inputs:**
- Full portfolio state (value, cash, P&L per position, vs SPY benchmark)
- Seed beliefs + in-run beliefs and lessons (persistent memory)
- Call 1 output (news assessment, theme impacts)
- Call 2 output (technical screens, entry/exit candidates)
- Fundamentals for holdings + candidates
- Watching theses (stopped-out positions available for re-entry)

**Core Position Review (CRITICAL):**
At each Call 3, Claude must explicitly review every core position:
- Is the thesis still valid? What evidence supports/contradicts it?
- Is OBV still confirming the trend?
- Should we hold, add to the position, or close?
- A core position at -8% with an intact thesis should be HELD.
- A core position at +5% with a deteriorating thesis should be CLOSED.

**Outputs:**
- Trade decisions (buy, sell, short, cover, reduce) with tier designation (scout/core)
- Thesis updates (new, strengthening, weakening, closed)
- Lesson updates (new lessons, score changes)
- Theme updates (score changes, new themes)
- Weekly summary narrative

**Execution:**
- Trades placed via Alpaca API (paper or live)
- Scout positions: bracket orders with Claude's stop + target prices
- Core positions: market orders, 30% catastrophic safety net only
- Stopped-out scouts move to WATCHING

**Skip condition:** If Call 2 flagged no actionable setups AND no core positions need review AND no breaking news, Call 3 can be skipped. But never skip if any core positions are open.

## Weekly / Monthly Cadence

### Weekly (Every Friday after close)
- Call 3 always runs on Friday regardless of skip conditions
- Full portfolio review including all core position thesis evaluations
- Review watching theses — any worth re-entering?

### Monthly (3rd Friday)
- Belief consolidation — lessons → beliefs (max 5)
- Theme pruning — remove low-scoring themes, check against world_view.md
- World view consolidation — daily observations → macro regime summary
- **Forward outlook update** — "Where is the world in 12-18 months? What should we own?" This is the most important monthly task. Core positions should align with the forward outlook.
- Performance review — return vs SPY, systematic issues
- Universe cleanup — remove stale tickers

## Watchlist Management

Buffer between discovery and positions:
- **Max 15 stocks** on the watchlist at any time
- Call 1 proposes additions based on news (including outside the universe)
- Call 2 screens watchlist daily for entry setups
- Stocks stay until: entry triggered, or removed after 2 weeks with no setup
- Holdings drop off watchlist when position opened

## Universe Growth

The 77-stock universe is the starting point, not the limit. The tradeable universe is effectively unlimited — any stock that makes news can be screened and traded.

**How new stocks enter:**
1. Call 1 flags a ticker from news
2. Call 2 screens it — downloads bars, fetches fundamentals, runs technicals
3. If Claude adds it to the watchlist, it stays in rotation
4. If watchlisted 3+ times, gets added to permanent universe

**Cost impact:** Universe size doesn't affect daily cost. Only holdings + watchlist get screened daily.

## World View

Persistent macro narrative giving Claude coherent context.

### File: `world_view.md`

```
# World View

## Current Macro Regime (updated monthly)
Risk-off environment. Fed higher-for-longer, tariff uncertainty, AI capex cycle intact but
market pricing in efficiency gains. Defensive sectors outperforming. Energy range-bound.

## Forward Outlook — 12-18 Months (updated monthly)
AI capex supercycle continues through 2026. The second derivative is power demand — nuclear
and data center infrastructure is 18 months behind the GPU cycle (CEG, VST). GLP-1 adoption
accelerates as insurance coverage expands — LLY/NVO TAM doubles. Tariff regime creates
structural winners (domestic manufacturing) and losers (import-dependent retail). If rate
cuts materialise in H2 2025, real estate and growth will re-rate sharply.

Positioning implications:
- Core: AI infrastructure (NVDA, AVGO, TSM) — hold through volatility, pyramid on dips
- Emerging: Nuclear/power (CEG, VST) — entering the acceleration phase GPU went through in 2023
- Watch: Rate-sensitive growth — not yet, but prepare for regime shift when Fed pivots
- Avoid: Import-dependent consumer (NKE, TGT) — structural headwind until tariff policy reverses

## This Week's Observations (rolling 5-day window)
- Mar 24: Trump tariff announcement, broad selloff
- Mar 25: Tech bounced on oversold RSI, OBV rising in NVDA/AVGO
- Mar 26: Quiet day, no catalysts
```

### Update cadence
- **Daily (Call 1):** Appends one observation line. Rolling 5-day window.
- **Monthly:** Claude consolidates daily observations into macro regime summary. Daily entries reset.
- **Monthly:** Claude updates the Forward Outlook — "Based on current trends and themes, what does the world look like in 12-18 months? What are the positioning implications?" This is the Druckenmiller edge: positioning today for where the world is going, not where it is.
- **Max size:** ~700 words total (regime ~200, forward outlook ~300, daily observations ~200).

### Why the Forward Outlook matters

Without it, Claude is reactive — it sees tariff news and reacts. With it, Claude is predictive — it anticipated tariff risk from election polling 6 months ago and was already positioned. The forward outlook forces Claude to think structurally:

- What are the 2-3 biggest macro forces playing out over the next 18 months?
- Which sectors/companies are best positioned for that world?
- What would change this view? (invalidation conditions for the outlook itself)

The forward outlook feeds directly into core position selection. A core position should be aligned with the 12-18 month view, not just this week's news. This is what separates "buying NVDA because RSI is oversold" from "owning NVDA because AI capex is a multi-year structural shift and we're in the third inning."

### Relationship to themes
World view is the **input**, themes are the **output**, forward outlook is the **direction**:

```
News events → World View (macro regime) → Forward Outlook (12-18 months)
                                              ↓
                                        Theme discovery/scoring → Position decisions
```

Themes that align with the forward outlook get higher conviction. Themes that contradict it get questioned. A theme at score 4 that contradicts the forward outlook should trigger a review — either the theme is wrong or the outlook needs updating.

## System Architecture Diagram

```
╔══════════════════════════════════════════════════════════════════════════╗
║                         DAILY FLOW                                      ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ┌─────────────────────┐                                                 ║
║  │  CALL 1 (Pre-market) │                                                ║
║  │  News & Macro Scan   │                                                ║
║  │                       │                                                ║
║  │  Reads:               │     ┌──────────────────┐                      ║
║  │  - All news (global)  │────▶│  world_view.md   │ (appends daily)      ║
║  │  - themes.md          │     └──────────────────┘                      ║
║  │  - holdings list      │                                                ║
║  │                       │     ┌──────────────────┐                      ║
║  │  Outputs:             │────▶│  Flagged tickers  │ (in-universe + new)  ║
║  │  - Macro assessment   │     │  Theme impacts    │                      ║
║  │  - Theme signals      │     └────────┬─────────┘                      ║
║  └─────────────────────┘              │                                  ║
║                                        ▼                                  ║
║  ┌─────────────────────┐     ┌──────────────────┐                        ║
║  │  CALL 2 (Mid-day)   │◀────│  Flagged tickers  │                       ║
║  │  Technical & Funda-  │     └──────────────────┘                       ║
║  │  mental Screen       │                                                 ║
║  │                       │     Screens:                                   ║
║  │  - Holdings technicals│     - Holdings (~5-8)                          ║
║  │  - Watchlist screen   │     - Watchlist (~15)                          ║
║  │  - New ticker fetch   │     - New discoveries (~5)                     ║
║  │                       │                                                ║
║  │  Outputs:             │     ┌──────────────────┐                      ║
║  │  - Entry candidates   │────▶│  Proposed actions  │                     ║
║  │  - Exit alerts        │     │  (scout vs core)   │                     ║
║  │  - Short candidates   │     └────────┬─────────┘                      ║
║  └─────────────────────┘              │                                  ║
║                                        ▼                                  ║
║  ┌─────────────────────┐     ┌──────────────────┐                        ║
║  │  CALL 2.5 (Hourly)  │     │  Breaking news?   │                       ║
║  │  Alert Scanner       │────▶│  No → wait        │                       ║
║  │  (lightweight poll)  │     │  Yes → emergency   │                      ║
║  └─────────────────────┘     │        Call 3      │                       ║
║                               └────────┬─────────┘                       ║
║                                        ▼                                  ║
║  ┌─────────────────────┐                                                 ║
║  │  CALL 3 (Late PM)   │◀─── Full context:                               ║
║  │  Decision & Execute  │     - Portfolio state                           ║
║  │                       │     - world_view.md                            ║
║  │  Reads ALL memory:    │     - beliefs.md + seed_beliefs.md             ║
║  │  - beliefs.md         │     - lessons_learned.md                       ║
║  │  - seed_beliefs.md    │     - themes.md                                ║
║  │  - lessons_learned.md │     - active_theses.md (+ watching section)    ║
║  │  - themes.md          │     - portfolio_ledger.md                      ║
║  │  - active_theses.md   │     - Call 1 + Call 2 outputs                  ║
║  │  - portfolio_ledger.md│     - Fundamentals                             ║
║  │  - world_view.md      │                                                ║
║  │                       │                                                ║
║  │  Outputs:             │     ┌──────────────────┐                      ║
║  │  - Trade orders ──────│────▶│  Alpaca API       │                      ║
║  │  - Thesis updates ────│────▶│  active_theses.md │ (+ watching section) ║
║  │  - Lesson updates ────│────▶│  lessons_learned  │                      ║
║  │  - Theme updates ─────│────▶│  themes.md        │                      ║
║  │  - Ledger updates ────│────▶│  portfolio_ledger │                      ║
║  └─────────────────────┘     └──────────────────┘                        ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════╗
║                      MEMORY HIERARCHY                                    ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  PERMANENT (persist across all time)                                     ║
║  ┌────────────────────────────────────────────┐                          ║
║  │  seed_beliefs.md (max 5)                   │  Cross-regime principles ║
║  │  Updated: After each sim/live period       │  Consolidated by Claude  ║
║  │  Lifespan: Permanent (regime-validated)    │  across multiple regimes ║
║  ├────────────────────────────────────────────┤                          ║
║  │  beliefs.md (max 5)                        │  In-run principles       ║
║  │  Updated: Monthly                          │  e.g. "Institutional     ║
║  │  Lifespan: Months to years                 │  Flow Primacy"           ║
║  └────────────────────────────────────────────┘                          ║
║                          ▲ consolidated from                             ║
║  MEDIUM-TERM (weeks to months)                                           ║
║  ┌────────────────────────────────────────────┐                          ║
║  │  lessons_learned.md (max 15, scored 1-5)   │  Tactical rules          ║
║  │  Updated: Daily (Call 3)                   │  e.g. "Triple distrib    ║
║  │  Lifespan: Weeks (evicted when cap hit)    │  = exit"                 ║
║  ├────────────────────────────────────────────┤                          ║
║  │  themes.md (max 8, scored 1-5)             │  Investment themes       ║
║  │  Updated: Daily (Call 3)                   │  e.g. "AI Infra [4]"    ║
║  │  Lifespan: Weeks to months                 │                          ║
║  ├────────────────────────────────────────────┤                          ║
║  │  active_theses.md (max 8 active + 5 watch) │  Per-position theses    ║
║  │  Updated: Daily (Call 3)                   │  ACTIVE: full thesis     ║
║  │  SCOUT: mechanical stop/target             │  CORE: thesis-based exit ║
║  │  Stopped out → WATCHING (6-review expiry)  │  WATCHING: 1-liner       ║
║  │  Claude closes → DELETED (invalidated)     │                          ║
║  └────────────────────────────────────────────┘                          ║
║                                                                          ║
║  SHORT-TERM (days)                                                       ║
║  ┌────────────────────────────────────────────┐                          ║
║  │  world_view.md                             │  Macro narrative         ║
║  │  Updated: Daily (Call 1) + Monthly summary │  Rolling 5-day window    ║
║  │  Lifespan: Daily entries roll off          │  + monthly regime        ║
║  ├────────────────────────────────────────────┤                          ║
║  │  portfolio_ledger.md                       │  Current positions       ║
║  │  Updated: Daily (prices) + Call 3 (trades) │  Real-time P&L           ║
║  │  Lifespan: Reflects current state          │  Tier label (scout/core) ║
║  ├────────────────────────────────────────────┤                          ║
║  │  watchlist.md (max 15)                     │  Stocks being tracked    ║
║  │  Updated: Daily (Call 2)                   │  Pre-position staging    ║
║  │  Lifespan: 2 weeks max per ticker          │                          ║
║  └────────────────────────────────────────────┘                          ║
║                                                                          ║
║  REFERENCE (read-only in decisions)                                      ║
║  ┌────────────────────────────────────────────┐                          ║
║  │  quarterly_summaries.md (max 8)            │  Compressed history      ║
║  │  fundamentals cache (JSON per ticker)      │  Financial data          ║
║  └────────────────────────────────────────────┘                          ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝

Information flows DOWN through the hierarchy:
  News → World View → Themes → Theses → Positions
  Lessons → Beliefs (consolidation flows UP)
  Seed beliefs inform all decisions (loaded at start for live trading)
```

## Execution Details

### Order Types
- **Market orders** for core position entries and urgent exits
- **Limit orders** for scout entries (set at current price or slight discount)
- **Bracket orders** for scout positions (Claude's stop + target enforced mechanically)
- **Safety-net orders** for core positions (30% catastrophic stop only)

### Risk Rules
- Max 8 positions (prefer 5-6)
- Min 20% cash reserve
- Scout positions: capped at 5% (low) / 8% (medium), mechanical stops + targets
- Core positions: uncapped allocation, 30% catastrophic safety net only, Claude manages exits
- Pyramiding: add to winning core positions by re-submitting with higher target allocation
- Max 30% total short exposure
- Max 15% single short position
- Stopped-out scouts move to WATCHING (max 5, 6-review expiry)
- Profitability gate: unprofitable companies cannot reach "highest" confidence

## Future: Options Integration (Phase 4+)

Options would add asymmetric risk/reward capability — the Burry dimension of the strategy. Not yet implemented but the architecture supports it:

### Why Options Matter for This Strategy
- **Defined risk:** Buy a put for $500, maximum loss is $500 regardless of how wrong you are. But if the stock drops 40%, the payoff could be $5,000+. This is true asymmetry.
- **Leverage without margin:** Control $15,000 of stock exposure with $1,500 in options premium. This amplifies core conviction bets without increasing portfolio risk.
- **Hedging core positions:** Buy protective puts on core long positions instead of setting stops. The position can ride through volatility without being stopped out, and the put limits downside.
- **Income on holdings:** Sell covered calls on core positions to generate income while waiting for thesis to play out.

### Potential Options Strategies
| Strategy | Use Case | Risk Profile |
|----------|----------|-------------|
| Long puts | Bearish conviction (replaces short selling) | Defined risk, unlimited reward |
| Long calls | Bullish conviction on catalyst (earnings, policy) | Defined risk, unlimited reward |
| Protective puts | Hedge core long positions | Costs premium, removes stop-out risk |
| Covered calls | Income on core positions in sideways markets | Caps upside, generates income |
| Put spreads | Cheaper bearish bets with capped reward | Defined risk, defined reward |
| Straddles | Pre-earnings or pre-policy event volatility plays | Defined risk, profits from big moves either direction |

### Implementation Considerations
- Alpaca supports options trading on live accounts
- Options data (chains, Greeks, IV) would need a new data source (possibly CBOE or options-specific API)
- Claude would need options-specific prompt sections (IV rank, delta, theta decay, expiry management)
- Position sizing changes: options are sized by premium paid, not notional exposure
- New risk rules needed: max % of portfolio in options premium, max single options bet

### How It Fits the Druckenmiller Strategy
- **Scout equivalent:** Buy a small OTM call/put to test a thesis for $200-500. If it works, scale into a core equity position.
- **Core equivalent:** Long dated (3-6 month) ATM calls on highest conviction ideas. 5-10% of portfolio in premium. Asymmetric payoff.
- **Hedging:** Protective puts on core equity positions instead of wide stops. Allows holding through volatility without stop-out risk.

## API Costs & Efficiency

| Call | Frequency | Estimated Claude tokens | Daily cost |
|------|-----------|------------------------|------------|
| Call 1 | Daily | ~2-3k input, ~500 output | Low |
| Call 2 | Daily | ~5-8k input, ~1k output | Medium |
| Call 2.5 | Every 30-60 min (lightweight) | ~500 input, ~100 output (most are no-ops) | Very low |
| Call 3 | Daily (skippable) | ~10-15k input, ~2k output | High |

Total: ~3-4 Claude calls per day on active days, 2 on quiet days.

## Migration from Sim to Live

### Phase 1: Paper Trading
- Same Alpaca API, `paper=True`
- Daily automated runs via cron/scheduler
- Human reviews all core position decisions before execution (approval gate)
- Scout positions can auto-execute
- Run for 4-8 weeks minimum
- Seed beliefs loaded from sim runs

### Phase 2: Semi-Automated Live
- Real money, small account ($5-10k)
- Human approves all core position entries (>10% allocation)
- Scout positions auto-execute
- Weekly human review of beliefs, themes, and core position theses
- Emergency kill switch if drawdown exceeds 25%

### Phase 3: Fully Automated Live
- Remove human approval gate for scouts
- Core positions still require human confirmation for entry (not exit)
- All exits auto-execute (including thesis-invalidation closes)
- Human monitors weekly performance reports
- Emergency kill switch if drawdown exceeds 30%

### Phase 4: Options Integration
- Add options capability (see Future section above)
- Start with protective puts on core positions (hedging)
- Graduate to directional options (long calls/puts) as conviction plays
- Eventually use options as scout positions (cheap thesis tests)

## Technical Implementation

### Existing Components
- `src/strategy/decision_engine.py` — Core prompt builder with two-tier position logic
- `src/strategy/thesis_manager.py` — Memory system (beliefs, lessons, themes, watching theses)
- `src/strategy/risk_v3.py` — Position sizing with core/scout tiers and confidence caps
- `src/strategy/belief_consolidator.py` — Cross-regime belief consolidation
- `src/analysis/technical.py` — All technicals (RSI, MACD, OBV, ADX, HV, ATR%)
- `src/research/fundamentals.py` — Fundamental data integration (yfinance, 45-day lag)
- `src/research/news_client.py` — Alpaca news API
- `src/execution/broker.py` — Alpaca trading client
- `src/simulation/thesis_sim.py` — Backtesting engine with core/scout execution

### New Components Needed for Live
- `src/live/scheduler.py` — Cron-based daily flow orchestration (3 calls + alerts)
- `src/live/news_scanner.py` — Breaking news alert system (Call 2.5)
- `src/live/watchlist.py` — Watchlist management (add/remove/age out)
- `src/live/executor.py` — Alpaca order placement with core/scout bracket logic
- `src/live/reporter.py` — Daily/weekly performance reports
- `src/live/options.py` — Options chain data + Greeks (Phase 4)
