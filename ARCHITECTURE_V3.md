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
| **Stock discovery** | Static watchlist only | News-driven discovery + curated universe |

---

## V3 High-Level Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                     RESEARCH LAYER (Weekly)                      │
│              "What's happening in the world?"                    │
│                                                                  │
│  Layer 1 — Curated Universe (~50-100 themed stocks)             │
│  Pre-loaded at sim start. Claude picks from quality candidates. │
│                                                                  │
│  Layer 2 — News-Driven Discovery (Alpaca News API)              │
│  50 articles/week. Surfaces trending tickers not in universe.   │
│  On-demand bar download for newly discovered stocks.            │
│                                                                  │
│  Layer 3 — MCP Research Agent (Live Trading Only)               │
│  Claude uses Alpaca MCP for deep dives: fundamentals, price     │
│  history, market conditions. Not available in backtests.        │
│                                                                  │
│  Output: World State Brief + Emerging Opportunities             │
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
│  • Curated Universe (themed stock candidates)                    │
│                                                                  │
│  Output: Updated Investment Theses (long AND short)              │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                   DECISION LAYER (Weekly)                         │
│              "What trades do we make?"                            │
│                                                                  │
│  Claude outputs concrete actions:                                │
│  • BUY ticker, allocation %, thesis, invalidation, horizon       │
│  • SHORT ticker, allocation %, thesis (structural decline)       │
│  • SELL ticker, reason (thesis broken / rebalance / take profit) │
│  • REDUCE ticker, reason (thesis weakening)                      │
│  • HOLD ticker, reason (thesis intact)                           │
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
│  • RiskManagerV3 enforces: max position %, max exposure, cash    │
│  • Calculates exact share count from allocation %                │
│  • Places orders via Alpaca API                                  │
│  • Sets wide catastrophic stop (18%) — safety net only           │
│  • No tight ATR brackets — exits are thesis-driven               │
│  • Logs everything to memory files                               │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                     MEMORY LAYER (Persistent)                    │
│                                                                  │
│  ┌─────────────────┐  ┌──────────────────┐                      │
│  │ Active Theses   │  │ Portfolio Ledger  │                      │
│  │ (max ~15)       │  │ (current holds)   │                      │
│  │ Why we own what │  │ What we actually  │                      │
│  │ we own, when to │  │ hold, entry       │                      │
│  │ exit            │  │ prices, dates     │                      │
│  └─────────────────┘  └──────────────────┘                      │
│                                                                  │
│  ┌─────────────────┐  ┌──────────────────┐                      │
│  │ Quarterly       │  │ Lessons Learned   │                      │
│  │ Summaries       │  │ (persistent)      │                      │
│  │ Compressed      │  │ Rules discovered  │                      │
│  │ history         │  │ through experience│                      │
│  └─────────────────┘  └──────────────────┘                      │
│                                                                  │
│  ┌──────────────────────────────────────────┐                   │
│  │ Simulation Log (backtest history only)   │                   │
│  └──────────────────────────────────────────┘                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Research Architecture

### Layer 1: Curated Universe (Immediate — Simulation + Live)

A themed stock universe of ~50-100 quality companies, pre-loaded at sim start. This gives Claude real choices from day 1 without needing to discover them through random news articles.

**Structure:**

```yaml
# config/default.yaml
universe:
  ai_technology:
    - NVDA    # GPU/AI infrastructure leader
    - AVGO    # Networking chips, custom AI silicon
    - AMD     # GPU competitor, data center growth
    - MSFT    # Azure AI, OpenAI partnership
    - GOOGL   # AI research, cloud, search
    - AMZN    # AWS, logistics automation
    - META    # AI research, social media
    - CRM     # Enterprise AI/SaaS
    - PLTR    # AI/government analytics
    - CRWD    # Cybersecurity (AI-adjacent)
    - SMCI    # AI server infrastructure
    - ARM     # Chip design, AI edge computing
    - TSM     # Foundry — makes everyone's chips
    - SNOW    # Cloud data platform
    - MU      # Memory chips for AI workloads

  healthcare_aging:
    - LLY     # GLP-1 leader (Mounjaro/Zepbound)
    - NVO     # GLP-1 competitor (Ozempic/Wegovy)
    - UNH     # Health insurance + Optum
    - JNJ     # Pharma + medical devices
    - ABBV    # Immunology, oncology
    - ISRG    # Surgical robotics
    - DXCM    # Continuous glucose monitoring
    - TMO     # Life sciences tools
    - VRTX    # Gene therapy, rare diseases
    - REGN    # Biotech, immunology

  energy_climate:
    - XOM     # Oil major, energy transition
    - CVX     # Oil major, hydrogen/CCS
    - ENPH    # Solar microinverters
    - FSLR    # Solar panels (US manufacturing)
    - NEE     # Largest US renewable utility
    - VST     # Nuclear + renewable power
    - CEG     # Nuclear power (AI data centers)
    - LNG     # LNG exports, energy security
    - OXY     # Oil + carbon capture
    - PLUG    # Hydrogen fuel cells

  finance:
    - JPM     # Largest US bank
    - GS      # Investment banking, trading
    - V       # Payment networks
    - MA      # Payment networks
    - BLK     # Asset management
    - SQ      # Fintech, Cash App
    - COIN    # Crypto exchange
    - SOFI    # Digital banking
    - HOOD    # Retail brokerage
    - SCHW    # Brokerage, wealth management

  consumer_inequality:
    - COST    # Membership warehouse, defensive
    - WMT     # Discount retail, ecommerce
    - AMZN    # Ecommerce (also in AI)
    - TGT     # Value retail
    - DG      # Dollar stores, low-income consumer
    - LVMH    # Luxury goods (wealth gap)
    - NKE     # Consumer brands, global
    - SBUX    # Consumer discretionary
    - CMG     # Fast casual dining
    - HD      # Home improvement, housing cycle
```

**Key design decisions:**
- ~50 stocks across 5 themes — broad enough for discovery, narrow enough for quality
- All pre-loaded with historical price data at sim start
- Claude gets the full universe list in its prompt so it knows what's available
- Stocks can appear in multiple themes (e.g. AMZN in AI + consumer)
- Universe updated manually between sim runs, not during

### Layer 2: News-Driven Discovery (Current — Simulation + Live)

Alpaca's `/v1beta1/news` endpoint surfaces trending tickers outside the curated universe.

**How it works:**
1. Fetch 50 broad articles per review period
2. Extract all ticker symbols mentioned
3. Tickers with 2+ mentions that aren't in the universe = "Emerging Opportunities"
4. Claude sees these in the world state brief
5. If Claude decides to buy, bars are downloaded on-demand

**Source:** Alpaca News API (Benzinga)
- Date filtering works correctly (critical for backtesting)
- All articles are from Benzinga (financial source, no noise filtering needed)
- Paginated via `next_page_token`
- Auth: `APCA-API-KEY-ID` + `APCA-API-SECRET-KEY` headers

**This catches:**
- IPOs and newly public companies getting buzz
- Penny stocks with breakout momentum
- Sector rotation signals (e.g. suddenly many energy articles)
- M&A targets

### Layer 3: Alpaca MCP Research Agent (Future — Live Trading Only)

The most powerful research capability. Claude uses Alpaca MCP tools to actively explore:
- Look up fundamentals (market cap, P/E, revenue growth)
- Check price history and technicals for unfamiliar stocks
- Validate that suggested tickers exist and are tradeable
- Research entire sectors ("show me all semiconductor stocks")

**Why live-only:** The MCP returns current (real-time) data. In a simulation of Jan 2024, asking "what's SMCI's market cap?" would return March 2026 data — that's future knowledge contamination. MCP research is only safe when the current date matches the decision date.

**Implementation approach:** A research sub-agent that runs before the weekly review:
1. Claude gets the world state brief (news + discovery tickers)
2. Research agent gets ~60 seconds to use MCP tools to investigate interesting opportunities
3. Findings are appended to the world state before the main investment review
4. This adds ~60 seconds per weekly review but dramatically improves research quality

**Timeline:** Build after the curated universe proves out in simulation. Deploy with live paper trading.

---

## Memory Architecture

Four persistent files that give Claude months/years of context in ~4 pages of tokens.

### 1. Active Theses — `data/active_theses.md`

Contains every active investment thesis with clear reasoning and exit conditions.

**Rules:**
- Max 15 active theses
- Each thesis must have explicit invalidation conditions
- Reviewed weekly — Claude updates status and notes
- When a thesis is invalidated → triggers a SELL decision

### 2. Portfolio Ledger — `data/portfolio_ledger.md`

Simple factual record of what we hold. Updated after every trade.

### 3. Quarterly Summaries — `data/quarterly_summaries.md`

Compressed history. After each quarter, Claude writes a summary. Max 8 summaries (2 years).

### 4. Lessons Learned — `data/lessons_learned.md`

Permanent, distilled wisdom. Only grows over time. Claude adds lessons organically as it discovers patterns through experience.

### 5. Simulation Log — `data/simulation_log.md`

Running log of every backtest run. **NOT included in Claude's context during backtests** to prevent future knowledge leakage. For human reference only.

### Memory at Decision Time

```
Context Window:
├── Active Theses (~1.5 pages)      — "Here's what we believe and why"
├── Portfolio Ledger (~0.5 pages)    — "Here's what we actually own"
├── Quarterly Summaries (~1 page)    — "Here's our track record"
├── Lessons Learned (~0.5 pages)     — "Here's what we've learned"
├── World State Brief (~1 page)      — "Here's what's happening now"
├── Curated Universe (~0.5 pages)    — "Here's what we can buy"
└── Technicals Summary (~0.5 pages)  — "Here are the timing signals"
Total: ~6 pages — well within Claude's context window
```

---

## Decision Engine

### Weekly Review Process

Every 5 trading days (simulation) or Saturday (live):

**Step 1: Research**
- Fetch news from Alpaca News API (date-filtered for backtesting)
- Build world state brief: macro headlines, sector news, portfolio news, emerging opportunities
- Build technicals summary for holdings + universe sample

**Step 2: Claude Review**
- Claude receives full memory context + fresh research
- Anti-future-knowledge prompt guard for backtesting
- Outputs JSON: world assessment, thesis updates, new/close/reduce positions, lessons

**Step 3: Execute**
- Risk manager validates all decisions (allocation limits, cash reserve, exposure caps)
- Calculate exact shares from allocation percentages
- Place orders (Alpaca live / SimBroker for backtests)
- Set 18% catastrophic stops
- Update memory files

### Monthly Strategic Review

Deeper review every ~20 trading days:
- Evaluate theme performance over the quarter
- Consider adding or retiring themes
- Write quarterly summary at quarter-end
- Review and update lessons learned

### Shorting

Claude can short stocks facing structural headwinds (e.g. disrupted by AI, losing market share, secular decline). Shorts need the same discipline: explicit thesis, invalidation conditions, and allocation. Max 30% short exposure.

---

## Risk Management

### Position-Level

| Rule | Value |
|------|-------|
| Max single position | 10% of portfolio |
| Default position size | 5-8% |
| Catastrophic stop | 18% below entry |
| Max positions | 15 |
| Min cash reserve | 20% |

### Portfolio-Level

| Rule | Value |
|------|-------|
| Max drawdown pause | 25% from peak |
| Max short exposure | 30% |
| Max single-theme exposure | 40% |

### Thesis-Level

| Rule | Description |
|------|-------------|
| Every position needs a thesis | No "it looks cheap" trades |
| Every thesis needs invalidation | Know when to exit before you enter |
| Invalidated thesis = immediate exit | Don't hold hope positions |

---

## Backtesting with Guardrails

### Anti-Future-Knowledge

1. **Date-filtered news:** Alpaca News API with `start`/`end` params ensures Claude only sees headlines available on the simulation date
2. **Explicit system prompt:** "You are making decisions on {date}. You DO NOT know what happens after this date."
3. **No MCP in sims:** MCP returns current data, not historical — only used in live trading
4. **On-demand bars:** Newly discovered stocks get historical bars downloaded, not current prices
5. **Memory builds fresh:** Each sim run starts with clean memory files that evolve week by week

### Limitations

- Claude may still have implicit knowledge of events from training data
- Results are directional, not definitive — paper trading forward is the true test
- No intra-week trading (weekly cadence may miss optimal timing)
- Alpaca News API only provides Benzinga articles (no Reuters, Bloomberg, etc.)

---

## Project Structure

```
trading-bot/
├── src/
│   ├── research/
│   │   ├── news_client.py         # Alpaca News API client
│   │   └── world_state.py         # Aggregates news into structured brief
│   │
│   ├── strategy/
│   │   ├── thesis_manager.py      # Memory system (5 persistent files)
│   │   ├── decision_engine.py     # Claude review orchestration
│   │   └── risk_v3.py             # Allocation-based sizing, stops
│   │
│   ├── analysis/
│   │   └── technical.py           # RSI, SMA, MACD, Bollinger (timing hints)
│   │
│   ├── data/
│   │   ├── market.py              # Alpaca price/account data
│   │   └── watchlist.py           # Watchlist management
│   │
│   ├── execution/
│   │   └── broker.py              # Alpaca order execution
│   │
│   ├── simulation/
│   │   ├── thesis_sim.py          # V3 weekly-cadence simulation engine
│   │   ├── run_thesis_sim.py      # CLI runner
│   │   ├── sim_broker.py          # Simulated broker (long + short)
│   │   └── report.py              # Report generator
│   │
│   ├── storage/
│   │   ├── database.py            # SQLite operations
│   │   └── models.py              # Data classes
│   │
│   └── config.py                  # Config loading, API key access
│
├── config/
│   └── default.yaml               # All configurable parameters
│
├── data/
│   ├── v3_sim/                    # Sim-isolated memory files
│   ├── v3_results/                # Simulation output (JSON, CSV, TXT)
│   ├── active_theses.md           # Live investment theses
│   ├── portfolio_ledger.md        # Current holdings
│   ├── quarterly_summaries.md     # Compressed history
│   ├── lessons_learned.md         # Persistent rules
│   └── simulation_log.md          # Backtest history
│
├── tests/
│   ├── test_news_client.py        # Alpaca news client tests
│   ├── test_world_state.py        # World state aggregation tests
│   ├── test_thesis_manager.py     # Memory system tests
│   ├── test_decision_engine.py    # Decision engine tests
│   ├── test_risk_v3.py            # V3 risk manager tests
│   ├── test_thesis_sim.py         # Simulation engine tests
│   ├── test_sim_broker.py         # SimBroker tests
│   ├── test_broker.py             # Live broker tests
│   ├── test_technical.py          # Technical analysis tests
│   ├── test_technical_v2.py       # MACD/BB tests
│   ├── test_config.py             # Config tests
│   ├── test_database.py           # Database tests
│   ├── test_market.py             # Market data tests
│   ├── test_models.py             # Data model tests
│   └── test_watchlist.py          # Watchlist tests
│
├── ARCHITECTURE_V2.md             # V2 architecture (historical reference)
├── ARCHITECTURE_V3.md             # This file
└── CLAUDE.md                      # Project-specific Claude instructions
```

---

## Roadmap

### Done
- [x] Memory system (5 persistent markdown files)
- [x] Decision engine (Claude weekly reviews via CLI)
- [x] V3 risk manager (allocation-based sizing, 18% catastrophic stops)
- [x] Thesis simulation engine (weekly cadence, anti-future-knowledge)
- [x] Alpaca News API client (replaced Tiingo — date filtering works)
- [x] News-driven discovery (emerging opportunities + on-demand bar download)
- [x] Shorting support (long + short thesis-driven positions)
- [x] V2 cleanup (all V2 files removed, PositionPlan migrated to risk_v3)
- [x] Noise filtering (keyword blacklist, financial source prioritisation)
- [x] Text/JSON/CSV report output

### Next: Curated Universe
- [ ] Define ~50-100 themed stocks in config/default.yaml
- [ ] Pre-load all universe bars at sim start
- [ ] Include universe list in Claude's prompt context
- [ ] Build technicals for universe stocks (not just holdings)
- [ ] Run comparison sim: 5-stock watchlist vs 50-stock universe

### Future: Live Trading
- [ ] MCP research sub-agent (deep dives before weekly review)
- [ ] Live scheduler (Saturday weekly reviews)
- [ ] Real-time Alpaca execution
- [ ] Fundamental data integration (P/E, revenue growth, market cap)
- [ ] On-event reviews (Fed decisions, earnings, crashes)
- [ ] Performance dashboard
- [ ] Paper trade for 4-8 weeks before real capital

---

## Design Philosophy

**Claude as investor, not trader.** Claude doesn't react to daily price moves. It forms investment theses based on how the world is changing, then holds positions until the thesis plays out or breaks.

**The world drives decisions, not charts.** "AI capex growing 40% YoY" matters more than "RSI crossed 70." Charts help with timing; the world tells you what to own.

**Hold with conviction, exit with discipline.** Wide stops prevent catastrophe. Thesis invalidation prevents hope-holding. The combination means we stay in winning positions through volatility but cut losers when the reason we bought breaks.

**Memory makes us smarter over time.** Each quarter of experience gets compressed into lessons and summaries. A year from now, Claude's decisions will be informed by 4 quarters of what worked and what didn't.

**Three layers of discovery.** Curated universe for quality. News discovery for momentum. MCP research for depth. Each layer catches different kinds of opportunities.

**Simple beats complex.** V2 had 5 technical gates, sentiment scoring, parameter tuning, daily reviews. V3 has one question: "Given what's happening in the world, what should we own?"
