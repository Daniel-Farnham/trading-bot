# Trading Bot — Architecture V3: Thesis-Driven Investing

## Why V3?

V2 tried to be a technical swing trader — and the simulation proved that doesn't work for us. Over 6 months (Jan-Jun 2024), the bot made 37 trades with a 30% win rate and -2.1% return. Claude's own journal analysis identified the core problem:

> "The blockage is in code-level technical confirmation filters (MACD, trend, volume gates). The parameter tuning lever is fully exhausted."

**The real lesson:** We were fighting with the wrong weapons. Competing on technical analysis and trade frequency is Citadel's game. Our edge is something different — Claude can read the world, understand patterns, and form investment theses that play out over weeks and months.

### V2 → V3: What Changes

| | V2 | V3 |
|---|---|---|
| **Who decides** | Code (rules-based signals) | Claude (thesis-driven reasoning) |
| **Hold period** | 1-10 days | Weeks to quarters |
| **Signal source** | Sentiment score + 5 technical gates | World events + investment theses |
| **Exits** | ATR stop/target (mechanical) | Thesis invalidation + wide catastrophic stops |
| **Technicals** | Gate that blocks trades | Timing hint for entries |
| **News** | Per-stock FinBERT score | Macro + sector + stock (Claude reads directly) |
| **Reviews** | Every 7 days, tune thresholds | Weekly thesis updates, monthly strategic |

---

## V3 High-Level Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                     RESEARCH LAYER (Weekly)                      │
│              "What's happening in the world?"                    │
│                                                                  │
│  Sources:                                                        │
│  • Tiingo News API — macro headlines, sector news, geopolitics  │
│  • Alpaca MCP — stock data, fundamentals, market conditions     │
│  • Web search — broader world events, earnings, economic data    │
│                                                                  │
│  Output: World State Brief                                       │
│  "AI spending accelerating. Tariffs hitting China imports.       │
│   Oil rising on Middle East tensions. Unemployment up in mfg."  │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                     THESIS LAYER (Weekly)                         │
│              "What should we own and why?"                        │
│                                                                  │
│  Claude reviews:                                                 │
│  • World State Brief (fresh research)                            │
│  • Active Theses (current positions + reasoning)                 │
│  • Portfolio Ledger (what we hold, entry prices)                 │
│  • Quarterly Summaries (compressed history)                      │
│  • Lessons Learned (persistent rules)                            │
│                                                                  │
│  Output: Updated Investment Theses                               │
│  "AVGO: AI infra spending growing 40% YoY. Broadcom's chips     │
│   in every data center. Hold 3-6 months. Invalidation:          │
│   AI capex declines 2 consecutive quarters."                     │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                   DECISION LAYER (Weekly)                         │
│              "What trades do we make?"                            │
│                                                                  │
│  Claude outputs concrete actions:                                │
│  • BUY ticker, allocation %, thesis reference, hold horizon      │
│  • SELL ticker, reason (thesis broken / rebalance / take profit) │
│  • HOLD ticker, reason (thesis intact)                           │
│  • REDUCE ticker, reason (thesis weakening)                      │
│                                                                  │
│  Technicals used as timing hints:                                │
│  "Buy AVGO — thesis says yes, and RSI at 35 is a good entry"    │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                 EXECUTION LAYER (Mechanical — Code)              │
│              Risk checks → sizing → orders                       │
│                                                                  │
│  • RiskManager enforces: max position %, max exposure, cash      │
│  • Calculates exact share count from allocation %                │
│  • Places orders via Alpaca API                                  │
│  • Sets wide catastrophic stop (15-20%) — safety net only        │
│  • No tight ATR brackets — exits are thesis-driven               │
│  • Logs everything to database                                   │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                     MEMORY LAYER (Persistent)                    │
│                                                                  │
│  ┌─────────────────┐  ┌──────────────────┐                      │
│  │ Active Theses   │  │ Portfolio Ledger  │                      │
│  │ (max ~15)       │  │ (current holds)  │                      │
│  │                 │  │                  │                      │
│  │ Why we own what │  │ What we actually │                      │
│  │ we own, when to │  │ hold, entry      │                      │
│  │ exit, what      │  │ prices, dates    │                      │
│  │ invalidates it  │  │                  │                      │
│  └─────────────────┘  └──────────────────┘                      │
│                                                                  │
│  ┌─────────────────┐  ┌──────────────────┐                      │
│  │ Quarterly       │  │ Lessons Learned  │                      │
│  │ Summaries       │  │ (persistent)     │                      │
│  │                 │  │                  │                      │
│  │ Compressed      │  │ Rules discovered │                      │
│  │ history: what   │  │ through          │                      │
│  │ worked, what    │  │ experience       │                      │
│  │ didn't, key     │  │                  │                      │
│  │ macro context   │  │                  │                      │
│  └─────────────────┘  └──────────────────┘                      │
│                                                                  │
│  ┌──────────────────────────────────────────┐                   │
│  │ Simulation Log (backtest history)        │                   │
│  │                                          │                   │
│  │ Results, bugs, insights, and config      │                   │
│  │ from each simulation run — so we never   │                   │
│  │ repeat the same mistakes twice           │                   │
│  └──────────────────────────────────────────┘                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Memory Architecture

The key innovation. Four persistent files that give Claude months/years of context in ~4 pages of tokens.

### 1. Active Theses — `data/active_theses.md`

The most important file. Contains every active investment thesis with clear reasoning and exit conditions.

```markdown
## AVGO — AI Infrastructure
**Opened:** 2024-02-15 | **Allocation:** 8% | **Horizon:** 3-6 months

**Thesis:** Broadcom's networking chips are in every major data center build.
AI infrastructure spending growing 40% YoY. VMware acquisition adds
enterprise software recurring revenue. Custom AI chip business (XPUs)
expanding with major cloud customers.

**Entry Rationale:** Price pulled back 8% on broad market sell-off.
RSI at 38 suggested oversold. Thesis fundamentals unchanged.

**Invalidation Conditions:**
- AI capex spending declines for 2 consecutive quarters
- Loss of 2+ major cloud customers
- Gross margins drop below 60%

**Status:** ACTIVE
**Last Reviewed:** 2024-03-15
**Notes:** Q1 earnings confirmed thesis. Revenue up 34% YoY.

---

## AAPL — Ecosystem + Services Growth
**Opened:** 2024-01-10 | **Allocation:** 6% | **Horizon:** 6-12 months

**Thesis:** Services revenue growing 15% YoY with 80%+ margins.
2B active devices creates unmatched distribution platform.
Vision Pro may underwhelm near-term but positions for spatial computing.

**Invalidation Conditions:**
- iPhone unit sales decline >10% YoY for 2 quarters
- Services growth drops below 8%
- Regulatory action forces App Store fee reduction >50%

**Status:** ACTIVE — MONITOR (iPhone China weakness)
**Last Reviewed:** 2024-03-15
```

**Rules:**
- Max 15 active theses (prevents over-diversification)
- Each thesis must have explicit invalidation conditions
- Reviewed weekly — Claude updates status, notes, and decides if thesis still holds
- When a thesis is invalidated → triggers a SELL decision

### 2. Portfolio Ledger — `data/portfolio_ledger.md`

Simple factual record of what we hold. Updated after every trade.

```markdown
# Portfolio Ledger
**Last Updated:** 2024-03-15 | **Total Value:** $108,200 | **Cash:** $34,500

| Ticker | Shares | Entry Price | Entry Date | Thesis        | Alloc % | Unrealized P&L |
|--------|--------|-------------|------------|---------------|---------|----------------|
| AVGO   | 12     | $168.50     | 2024-02-15 | AI Infra      | 7.8%    | +$840 (+4.2%)  |
| AAPL   | 25     | $182.30     | 2024-01-10 | Ecosystem     | 6.2%    | +$320 (+0.7%)  |
| PLTR   | 45     | $22.10      | 2024-03-01 | AI/Gov Tech   | 4.5%    | -$90 (-0.9%)   |
| LLY    | 5      | $762.00     | 2024-02-28 | GLP-1/Pharma  | 5.2%    | +$1,100 (+2.9%)|

**Cash Position:** 31.9% ($34,500)
**Invested:** 23.7% across 4 positions
**Short Exposure:** 0%
```

### 3. Quarterly Summaries — `data/quarterly_summaries.md`

Compressed history. After each quarter, Claude writes a summary and old weekly entries are pruned.

```markdown
## Q1 2024 (Jan 1 - Mar 31)

**Performance:** +8.2% ($100,000 → $108,200)
**Best Position:** AVGO +22% (AI infrastructure thesis)
**Worst Position:** TSLA -8% (exited — EV competition thesis broken)
**Trades:** 8 buys, 3 sells, 2 thesis invalidations

**What Worked:**
- AI theme drove 70% of returns
- Holding through volatility (AVGO dropped 12% intra-quarter, recovered)
- Waiting for pullbacks to enter (RSI-based timing)

**What Didn't Work:**
- TSLA thesis was too optimistic on EV margins
- Entered COIN too early (crypto winter extended)

**Key Macro Context:**
- Fed held rates at 5.25-5.50%, signaled 3 cuts later in 2024
- AI spending accelerated — every major tech company increased capex
- Middle East tensions elevated but contained
- US employment remained strong, inflation sticky at 3.1%

**Lessons Extracted:**
- Defensive stocks (JNJ, UNH) don't generate enough movement
- High-beta momentum names suit longer holds when thesis is strong
- Don't fight the trend — TSLA taught us to exit when thesis breaks
```

### 4. Lessons Learned — `data/lessons_learned.md`

Permanent, distilled wisdom. Only grows over time — never deleted, only refined.

```markdown
# Lessons Learned

## Entry Rules
- Only buy when there's a clear thesis with explicit invalidation conditions
- Use technicals for timing (RSI < 40 = good entry), not as gates
- Don't chase — if a stock has run 20%+ without a pullback, wait

## Exit Rules
- Exit when the thesis breaks, not when the price dips
- Wide catastrophic stops (15-20%) prevent ruin but don't trigger on normal volatility
- Take partial profits at +25% to lock in gains while keeping upside

## Position Management
- Max 15 positions — more dilutes conviction and attention
- 5-10% per position depending on conviction
- Keep 25-35% cash for opportunities

## What Doesn't Work For Us
- Short-term swing trading (1-10 day holds) — insufficient edge
- Tight ATR-based stops — whipsawed out of good positions
- Parameter oscillation — changing thresholds every week made everything worse
- Defensive low-volatility stocks — not enough movement to matter
- Competing on technical analysis — quant funds do this better

## What Works For Us
- Theme-aligned positions held for weeks/months
- Buying quality companies on pullbacks (RSI timing)
- Letting winners run — AVGO's 22% gain came from patience
- Removing losers quickly when thesis breaks
- Concentrated portfolios in high-conviction ideas
```

### 5. Simulation Log — `data/simulation_log.md`

A running log of every backtest and simulation run. Captures what was tested, what happened, what we learned, and what to try differently next time. This file persists across all runs and is never cleared — it's the lab notebook.

```markdown
# Simulation Log

## Run #3 — V3 Thesis-Driven | 2024-01-01 to 2024-06-30
**Date Run:** 2026-03-15 | **Architecture:** V3 | **Initial Cash:** $100,000

**Config Snapshot:**
- Themes: AI/Automation, Climate, Aging Populations, Wealth Inequality
- Review cadence: weekly (every 5 trading days)
- Max positions: 15 | Default allocation: 6% | Stop: 18%
- News source: Tiingo (macro + sector + ticker)

**Results:**
- Final Value: $112,400 (+12.4%)
- Total Trades: 14 buys, 6 sells
- Win Rate: 71% (10/14 closed positions profitable)
- Max Drawdown: -6.2%
- Best Thesis: AVGO +22% (AI Infrastructure)
- Worst Thesis: COIN -12% (entered too early)

**Theses That Worked:**
- AI Infrastructure (AVGO, CRWD) — strong and consistent
- GLP-1 Pharma (LLY) — secular tailwind played out over 4 months

**Theses That Failed:**
- Crypto recovery (COIN) — thesis was premature, regulatory headwinds persisted
- EV momentum (TSLA) — competition thesis broke in Q1

**Bugs Found:**
- Tiingo date filter was off-by-one (pulling tomorrow's news)
- Portfolio ledger wasn't updating unrealized P&L between reviews

**Key Insights:**
- Weekly cadence is right — daily was too noisy, monthly too slow
- Holding through 5-10% drawdowns was correct when thesis held
- Theme concentration (60% AI) was risky but paid off in this period
- Anti-future-knowledge prompt worked — Claude didn't reference post-date events

**What To Try Next:**
- Test with monthly review cadence to compare
- Add on-event reviews for Fed decisions and earnings
- Try a bearish period (Q4 2018 or Q1 2020) to test thesis invalidation

---

## Run #2 — V2 Adapted | 2024-01-01 to 2024-06-30
**Date Run:** 2026-03-13 | **Architecture:** V2 | **Initial Cash:** $100,000

**Results:**
- Final Value: $97,947 (-2.1%)
- Total Trades: 37
- Win Rate: 30%

**Key Insights:**
- Technical gates blocked too many trades — signal drought from March onwards
- Parameter oscillation was the primary drag (17 changes, each made things worse)
- Short selling was broken (same-day open+close bug, portfolio_value bug)
- Simulated sentiment from price changes is a weak signal
- Claude identified bugs in its own safety cap logic

**Bugs Found:**
- Safety cap for negative params moved toward zero (abs() fix)
- Shorts instantly closed by sentiment exit on same day
- sell_threshold read from CONFIG not DB (Claude's changes ignored)
- portfolio_value didn't account for short liability (inflated by ~$8K)
- Drawdown circuit breaker blocked ALL activity including stop checks

---

## Run #1 — V2 Dry Run (No Adaptation) | 2024-01-01 to 2024-06-30
**Date Run:** 2026-03-12 | **Architecture:** V2 (no adapt) | **Initial Cash:** $100,000

**Results:**
- Final Value: $102,320 (+2.3%)
- Total Trades: 36
- Win Rate: 19.4%

**Key Insights:**
- NVDA and AAPL were the only consistently profitable tickers
- TSLA generated the most trades (14) but worst overall performance
- Without adaptation, parameters never improved
- Baseline for comparison with adapted runs
```

**Rules:**
- Append a new entry after every simulation run
- Include config snapshot so runs are reproducible
- Log bugs found — prevents re-discovering the same issues
- "What To Try Next" feeds into planning the next run
- Keep indefinitely — this is the experiment history

**IMPORTANT — Backtesting Isolation:**
The simulation log is **NOT** included in Claude's context during backtests.
It contains results from previous runs over the same periods, which would
leak future knowledge ("AVGO went up 22% in Q1"). During simulation, Claude
only sees the 4 in-sim memory files (theses, ledger, summaries, lessons)
which are built up fresh during that run. The simulation log is for YOUR
reference — comparing runs, planning next experiments, tracking bugs.
It can also be used by Claude in planning conversations (like architecture
discussions) but never during simulated trading decisions.

### Memory at Decision Time

When Claude makes a weekly decision, it receives all four files plus fresh research:

```
Context Window:
├── Active Theses (~1.5 pages)      — "Here's what we believe and why"
├── Portfolio Ledger (~0.5 pages)    — "Here's what we actually own"
├── Quarterly Summaries (~1 page)   — "Here's our track record and lessons"
├── Lessons Learned (~0.5 pages)    — "Here's what we've learned"
├── World State Brief (~1 page)     — "Here's what's happening now"
└── Technicals Summary (~0.5 pages) — "Here are the timing signals"
Total: ~5 pages — well within Claude's context window
```

This covers months or years of history without hitting token limits.

---

## Research Layer

### Tiingo News API — Macro & Sector Headlines

**Endpoint:** `https://api.tiingo.com/tiingo/news`

Tiingo provides 8,000-12,000 articles per day from financial news sites and blogs. Key features for us:

- **Tag-based search:** `?tags=inflation,tariffs,fed` for macro news
- **Ticker-based search:** `?tickers=aapl,nvda` for stock-specific
- **Date filtering:** `?startDate=2024-01-01&endDate=2024-01-07` (critical for backtesting)
- **Fields:** title, description, publishedDate, source, tickers, tags

**How we use it:**

```python
# Weekly macro research
macro_news = tiingo.get_news(tags=["economy", "fed", "tariffs", "unemployment", "inflation"])
sector_news = tiingo.get_news(tags=["ai", "semiconductors", "healthcare", "energy"])
portfolio_news = tiingo.get_news(tickers=current_holdings)

# Format into World State Brief for Claude
world_state = format_world_state(macro_news, sector_news, portfolio_news)
```

**For backtesting:** Date filters ensure Claude only sees news available on the simulation date. No future information leakage.

### Alpaca MCP — Stock Research

Available during weekly reviews for:
- Looking up current prices and fundamentals
- Checking account and position status
- Validating that suggested tickers exist and are tradeable
- Market calendar and status

### Web Search — Broader Context (optional)

For events that aren't in financial news feeds:
- Geopolitical developments
- Technology breakthroughs
- Regulatory changes
- Earnings calendars

---

## Decision Engine

### Weekly Review Process

Every week (Saturday for live, every N sim-days for backtesting):

**Step 1: Research**
```
Pull news from Tiingo (macro + sector + portfolio)
Pull current prices and technicals for holdings
Format into World State Brief
```

**Step 2: Claude Review**

Claude receives:
```
You are the Chief Investment Officer of a thesis-driven trading bot.
Your role is to decide WHAT to own based on how the world is changing.

MEMORY:
{active_theses}
{portfolio_ledger}
{quarterly_summaries}
{lessons_learned}

THIS WEEK'S RESEARCH:
{world_state_brief}

TECHNICAL TIMING DATA:
{technicals_summary for current and potential holdings}

YOUR THEMES:
1. AI/Automation — companies building or benefiting from AI
2. Climate Transition — clean energy, EVs, sustainability
3. Aging Populations — healthcare, pharma, medical devices
4. Wealth Inequality — financial services, affordable goods

GOAL: Long-term capital growth. Hold for weeks to quarters.
We are patient investors who buy quality companies aligned with
macro themes. We use pullbacks as entry opportunities.

TASKS:
1. Review world events — what's changed this week that matters?
2. Update each active thesis — still valid? stronger? weakening?
3. Should we open any new positions? (provide full thesis)
4. Should we close or reduce any positions? (thesis broken?)
5. Any new lessons learned?

Respond with JSON:
{
  "world_assessment": "Brief summary of what matters this week",
  "thesis_updates": [
    {"ticker": "AVGO", "status": "ACTIVE", "notes": "Q1 confirmed thesis"}
  ],
  "new_positions": [
    {
      "ticker": "CRWD",
      "action": "BUY",
      "allocation_pct": 6,
      "thesis": "Full thesis text...",
      "invalidation": "What would make us sell",
      "horizon": "3-6 months",
      "timing_note": "RSI at 32, good entry point"
    }
  ],
  "close_positions": [
    {"ticker": "TSLA", "reason": "EV margin thesis broken by competition"}
  ],
  "reduce_positions": [
    {"ticker": "AAPL", "new_allocation_pct": 4, "reason": "China weakness"}
  ],
  "lessons": ["New lesson if any"],
  "weekly_summary": "Brief narrative for the quarterly summary"
}
```

**Step 3: Execute**

Code processes Claude's decisions:
1. Validate all tickers exist
2. Run through RiskManager (max position %, cash reserve, total exposure)
3. Calculate exact shares from allocation percentages
4. Place orders via Alpaca
5. Set wide catastrophic stops (15-20% below entry for longs)
6. Update memory files (theses, ledger)

### Monthly Strategic Review

Deeper, less frequent review:
- Evaluate theme performance over the quarter
- Consider adding or retiring themes
- Write quarterly summary if at quarter-end
- Review and update lessons learned
- Larger allocation shifts if needed

### On-Event Review (Future Enhancement)

Triggered by significant news events:
- Fed rate decisions
- Major geopolitical events
- Earnings for held positions
- Market crashes (>3% single-day drop)

---

## Backtesting with Guardrails

### The Challenge

Claude knows what happened in 2024. If you ask "should I buy NVDA in January?", it knows NVDA went up 150%. We need to prevent information leakage.

### The Approach

1. **Date-filtered news only:** Tiingo API with `startDate` and `endDate` parameters ensures Claude only sees headlines available on the simulation date.

2. **Explicit system prompt:**
```
CRITICAL: You are making decisions on {current_sim_date}.
You DO NOT know what happens after this date.
Base your decisions ONLY on the news and data provided.
Do not reference any events after {current_sim_date}.
```

3. **No ticker price knowledge:** Don't tell Claude "NVDA is at $500 and will go to $900." Provide current price and technicals only.

4. **Mechanical execution:** Claude outputs decisions as JSON, code executes them identically to live trading. No special simulation logic.

5. **Memory persists across sim weeks:** As the simulation advances week by week, the memory files accumulate — Claude's theses evolve based on how positions perform, just like in live trading.

### Simulation Cadence

```
Sim Week 1 (Jan 1-7):
  → Pull Tiingo news for Jan 1-7
  → Claude forms initial theses, makes first buys
  → Code executes, updates portfolio ledger

Sim Week 2 (Jan 8-14):
  → Pull Tiingo news for Jan 8-14
  → Claude reviews theses + new news
  → Adjusts positions if needed

... continues weekly ...

Sim Week 26 (Jun 24-30):
  → Final review
  → Generate performance report
  → Compare against V2 and buy-and-hold benchmarks
```

### Limitations

- Claude may still have implicit knowledge of 2024 events from training data
- Results are directional, not definitive — paper trading forward is the true test
- Simulated news sentiment may differ from what a human would interpret
- No intra-week trading (weekly cadence may miss optimal entry/exit timing)

---

## Risk Management

### Position-Level

| Rule | Value | Why |
|------|-------|-----|
| Max single position | 10% of portfolio | No single thesis should sink us |
| Default position size | 5-8% | Room to add on conviction |
| Catastrophic stop | 15-20% below entry | Safety net, not a trading tool |
| Max positions | 15 | Concentration drives returns |
| Min cash reserve | 20% | Always have dry powder |

### Portfolio-Level

| Rule | Value | Why |
|------|-------|-----|
| Max drawdown pause | 25% from peak | Wider than V2 — long holds need room |
| Max single-theme exposure | 40% | Diversify across themes |
| Max short exposure | 20% | Shorts are tactical, not core |
| Quarterly rebalance | Check allocations | Prevent drift |

### Thesis-Level

| Rule | Description |
|------|-------------|
| Every position needs a thesis | No "it looks cheap" trades |
| Every thesis needs invalidation conditions | Know when to exit before you enter |
| Invalidated thesis = immediate exit | Don't hold hope positions |
| Max thesis age without review | 4 weeks | Stale theses get re-evaluated |

---

## New & Changed Modules

### New Files

| File | Purpose |
|------|---------|
| `src/research/tiingo.py` | Tiingo News API client — macro + sector + ticker news, date-filtered |
| `src/research/world_state.py` | Aggregates research into structured World State Brief for Claude |
| `src/strategy/thesis_manager.py` | CRUD for active theses, portfolio ledger, quarterly summaries, lessons |
| `src/strategy/decision_engine.py` | Calls Claude with full context, parses decisions, orchestrates execution |
| `src/simulation/thesis_sim.py` | Weekly-cadence simulation engine for backtesting thesis-driven strategy |
| `data/active_theses.md` | Live investment theses with invalidation conditions |
| `data/portfolio_ledger.md` | Current holdings with entry data |
| `data/quarterly_summaries.md` | Compressed historical performance and lessons |
| `data/lessons_learned.md` | Permanent rules discovered through experience |
| `data/simulation_log.md` | Running log of all backtest runs — results, bugs, insights |

### Changed Files

| File | Change |
|------|--------|
| `src/strategy/risk.py` | Wider stops, thesis-based exits, allocation-% sizing |
| `src/analysis/technical.py` | Keep — used as timing hints, not gates |
| `src/execution/broker.py` | Keep — still places orders mechanically |
| `src/data/market.py` | Keep — still need prices |
| `config/default.yaml` | New V3 parameters (hold horizons, thesis limits, Tiingo config) |

### Removed / Deprecated

| File | Why |
|------|-----|
| `src/analysis/sentiment.py` | Claude reads news directly — no need for FinBERT scoring |
| `src/strategy/signals.py` | Replaced by Claude's thesis-driven decisions |
| `src/adaptation/optimizer.py` | No more daily parameter tuning |
| `src/strategy/themes.py` | Themes are now part of Claude's reasoning, not a separate scoring system |

---

## Updated Config

```yaml
# config/default.yaml — V3

alpaca:
  base_url: "https://paper-api.alpaca.markets"
  data_url: "https://data.alpaca.markets"

tiingo:
  base_url: "https://api.tiingo.com/tiingo"
  # TIINGO_API_KEY set in .env

themes:
  - name: "AI/Automation"
    description: "Companies building or benefiting from AI, robotics, automation"
    keywords: ["ai", "semiconductors", "cloud", "robotics", "automation"]
  - name: "Climate Transition"
    description: "Clean energy, EVs, sustainability, grid infrastructure"
    keywords: ["solar", "wind", "ev", "battery", "clean energy", "grid"]
  - name: "Aging Populations"
    description: "Healthcare, pharma, medical devices, senior services"
    keywords: ["healthcare", "pharma", "biotech", "medical", "aging"]
  - name: "Wealth Inequality"
    description: "Financial services, fintech, discount retail, luxury"
    keywords: ["fintech", "banking", "retail", "luxury", "payments"]

portfolio:
  max_positions: 15
  default_allocation_pct: 6
  max_single_position_pct: 10
  min_cash_reserve_pct: 0.20
  max_theme_exposure_pct: 0.40
  catastrophic_stop_pct: 0.18        # 18% wide stop — safety net only
  max_drawdown_pct: 0.25             # Wider — long holds need room

review:
  weekly_budget_usd: 1.50            # Weekly Claude review budget
  monthly_budget_usd: 3.00           # Monthly strategic review
  max_news_articles: 50              # Tiingo articles per research cycle
  macro_tags:                         # Tags for macro news research
    - "economy"
    - "fed"
    - "inflation"
    - "tariffs"
    - "unemployment"
    - "geopolitics"
    - "earnings"

memory:
  theses_path: "data/active_theses.md"
  ledger_path: "data/portfolio_ledger.md"
  summaries_path: "data/quarterly_summaries.md"
  lessons_path: "data/lessons_learned.md"
  sim_log_path: "data/simulation_log.md"
  max_active_theses: 15
  max_quarterly_summaries: 8          # 2 years of history

simulation:
  review_cadence_days: 5              # Weekly in sim = every 5 trading days
  monthly_review_cadence_days: 20
```

---

## Implementation Phases

### Phase 1 — Memory System
1. Build `src/strategy/thesis_manager.py` — CRUD for all 4 memory files
2. Define markdown schemas for each file
3. Tests for read/write/update/truncation

### Phase 2 — Research Layer
1. Build `src/research/tiingo.py` — Tiingo News API client
2. Build `src/research/world_state.py` — aggregates news into brief
3. Date-filtered queries for backtesting support
4. Tests with mocked API responses

### Phase 3 — Decision Engine
1. Build `src/strategy/decision_engine.py` — the core Claude call
2. Prompt engineering for thesis-driven decisions
3. JSON parsing and validation of Claude's output
4. Integration with risk manager for execution
5. Tests for decision parsing and edge cases

### Phase 4 — Execution Updates
1. Update `src/strategy/risk.py` — allocation-% based sizing, wider stops
2. Update `src/execution/broker.py` — thesis-tagged orders
3. Wire decision engine → risk manager → broker
4. Tests for new position sizing logic

### Phase 5 — Simulation Engine
1. Build `src/simulation/thesis_sim.py` — weekly cadence replay
2. Date-filtered Tiingo news for each sim week
3. Memory files persist and evolve across sim weeks
4. Anti-future-knowledge system prompt
5. Performance report comparing against benchmarks

### Phase 6 — Backtest & Validate
1. Run Jan-Jun 2024 backtest
2. Compare V3 vs V2 vs buy-and-hold S&P 500
3. Analyze thesis quality and decision patterns
4. Iterate on prompts and risk parameters

### Phase 7 — Paper Trading
1. Wire into live scheduler (weekly Saturday reviews)
2. Connect real-time Tiingo news feed
3. Alpaca paper trading execution
4. Monitor for 4-8 weeks before any real capital

---

## Design Philosophy

**Claude as investor, not trader.** Claude doesn't react to daily price moves. It forms investment theses based on how the world is changing, then holds positions until the thesis plays out or breaks. This matches what Claude is actually good at — reasoning about complex, interconnected systems.

**The world drives decisions, not charts.** "Trump announces tariffs on China" matters more than "RSI crossed 70." Charts help with timing; the world tells you what to own.

**Hold with conviction, exit with discipline.** Wide stops prevent catastrophe. Thesis invalidation prevents hope-holding. The combination means we stay in winning positions through volatility but cut losers when the reason we bought breaks.

**Memory makes us smarter over time.** Each quarter of experience gets compressed into lessons and summaries. A year from now, Claude's decisions will be informed by 4 quarters of what worked and what didn't — without needing to fit all that history in one prompt.

**Simple beats complex.** V2 had 5 technical gates, sentiment scoring, parameter tuning, daily reviews. V3 has one question: "Given what's happening in the world, what should we own?" Everything else is execution detail.
