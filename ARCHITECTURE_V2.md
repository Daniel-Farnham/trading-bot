# Trading Bot — Architecture V2

## What Changed and Why

V1 works — the simulation runs, trades execute, and the basic adaptation layer tunes parameters. But Q1 2024 results revealed the limitations:

- **25% win rate** — the bot takes too many weak trades
- **Adaptation layer is stateless** — each `claude -p` call has zero memory of previous decisions
- **Claude can only tune numbers** — can't add/remove stocks, can't analyze macro context
- **Long-only** — when sentiment is negative, the bot just... sits there
- **Limited technicals** — RSI, SMA, ATR are good but not enough for timing

**The core thesis:** We can't beat hedge funds on speed or technical analysis. Our edge is Claude synthesizing macro narratives, identifying thematic trends, and matching stocks to those themes. The bot should be **theme-first**, with technicals as timing tools.

---

## V2 High-Level Flow

```
                         ┌─────────────────────────────┐
                         │     WEEKLY STRATEGIC REVIEW  │
                         │         "The CEO"            │
                         │                              │
                         │  • Reads strategy journal    │
                         │  • Explores via Alpaca MCP   │
                         │  • Discovers new stocks      │
                         │  • Updates watchlist          │
                         │  • Adjusts theme weights     │
                         │  • Sets weekly direction     │
                         └──────────┬──────────────────┘
                                    │ writes to
                                    ▼
┌─────────────┐     ┌──────────────────────────┐     ┌────────────────┐
│  SCHEDULER   │────▶│     STRATEGY JOURNAL     │◀────│ DAILY TACTICAL │
│ (every 30m)  │     │                          │     │    REVIEW      │
│              │     │  • Market observations   │     │                │
│              │     │  • Decision history       │     │ • Tune params  │
│              │     │  • Theme performance      │     │ • Flag stocks  │
│              │     │  • What worked/didn't     │     │ • Track impact │
│              │     └──────────────────────────┘     └────────────────┘
│              │                    │ provides context to
│              │                    ▼
│              │     ┌──────────────┐     ┌────────────────┐     ┌────────────┐
│              │────▶│  DATA LAYER  │────▶│ STRATEGY ENGINE │────▶│  EXECUTOR  │
│              │     │              │     │                │     │            │
│              │     │ • News feed  │     │ • Sentiment    │     │ • Alpaca   │
│              │     │ • Prices     │     │ • Technicals   │     │ • Longs    │
│              │     │ • Portfolio  │     │   (+ MACD, BB) │     │ • Shorts   │
└─────────────┘     └──────────────┘     │ • Signals      │     │ • Stops    │
                                         │   (long+short) │     └─────┬──────┘
                                         │ • Risk mgmt    │           │
                                         │ • Theme nudge  │           │
                                         └────────────────┘           │
                                                                      │
                    ┌──────────────┐                                   │
                    │  TRADE LOG   │◀──────────────────────────────────┘
                    │  (SQLite)    │
                    └──────────────┘
```

### The Loop (unchanged — runs every 30 minutes during market hours)

1. **Collect** — Pull latest news + prices + portfolio state
2. **Analyze** — Sentiment (FinBERT) + technicals (RSI, SMA, ATR, **MACD, Bollinger Bands**)
3. **Decide** — Generate **long AND short** signals, boosted by theme alignment
4. **Risk Check** — Position limits, exposure caps, **short-specific rules**
5. **Execute** — Place orders via Alpaca (longs and shorts)
6. **Log** — Record everything to SQLite
7. **Adapt** — Daily tactical review (with journal context) + **weekly strategic review**

---

## New Components

### 1. Strategy Journal

**File:** `data/strategy_journal.md` (persistent across sessions)
**Manager:** `src/adaptation/journal.py`

The journal gives Claude memory across stateless `claude -p` calls. After each review, an entry is appended:

```markdown
## Review — 2024-01-23 (Day 15) | Weekly Strategic

**Portfolio:** $100,400 (+0.4%) | 3 positions | Cash: $84,500
**Market State:** Tech rallying on AI earnings hype. NVDA leading S&P.
TSLA weak on delivery miss. Broad market risk-on.

**Theme Performance:**
- AI/Automation: +2.1% (NVDA carrying)
- Climate: -1.8% (TSLA dragging)
- Aging Populations: flat (no trades yet)

**Changes Made:**
- Raised sentiment_buy_threshold 0.6 → 0.65 (too many weak signals)
- Added AVGO to watchlist (AI/chip theme, strong momentum)
- Removed XOM (no theme alignment, flat performance)

**Reasoning:** Win rate at 25% suggests we're not selective enough.
Losses cluster in low-confidence entries. Being more selective should
improve quality. AVGO fits AI theme and has strong institutional flow.

**Tracking Previous Decisions:**
- [Jan 18] Widened stops → too early to evaluate
- [Jan 13] Added TSLA → underperforming, monitor 1 more week
```

**Key design decisions:**
- Markdown format (human-readable, easy for Claude to parse)
- Truncated to last **20 entries** to manage prompt size (~4K tokens)
- Tagged with review type (daily tactical vs weekly strategic)
- Includes explicit tracking of whether previous decisions worked
- The journal is the bot's institutional memory

---

### 2. Weekly Strategic Review — "The CEO"

**File:** `src/adaptation/weekly_review.py`
**Schedule:** Every Saturday (live) / every ~5 trading days (simulation)
**Uses:** Alpaca MCP for exploratory research

This is the highest-leverage addition. One comprehensive `claude -p` call that sets strategic direction.

#### What It Does

| Responsibility | How |
|---|---|
| **Stock Discovery** | Claude uses Alpaca MCP to research stocks matching themes. Can look up news, price history, check tradeability. |
| **Watchlist Management** | Adds up to 5 new stocks per week, removes up to 3 underperformers. Changes applied to config + runtime watchlist. |
| **Theme Evaluation** | Reviews which themes are generating alpha vs losing money. Adjusts theme weights. |
| **Macro Context** | Identifies market regime (risk-on/off, sector rotation, rate environment) and sets positioning. |
| **Strategic Direction** | Outputs a brief for the daily reviews: "This week, favor AI names, reduce TSLA exposure, watch for Fed minutes Wednesday." |
| **Journal Entry** | Appends a detailed entry to the strategy journal with full reasoning. |

#### Alpaca MCP Integration

The weekly review uses the Alpaca MCP server, giving Claude direct access to:
- Real-time and historical stock data
- News for any ticker
- Account and position information
- Market status and calendar

**Setup:**
```bash
# Install uv (includes uvx)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Initialize Alpaca MCP server
uvx alpaca-mcp-server init

# Register with Claude Code
claude mcp add alpaca --scope user --transport stdio uvx alpaca-mcp-server serve \
  --env ALPACA_API_KEY=your_key \
  --env ALPACA_SECRET_KEY=your_secret
```

**Why MCP for weekly but not daily:** The weekly review is exploratory — Claude needs to look up stocks we haven't considered, check news for sectors we're not tracking, validate that suggested tickers actually exist on Alpaca. This requires agency. Daily reviews are tactical and speed matters — pre-built prompts are faster.

#### The Weekly Prompt Structure

```
You are the Chief Investment Officer of an autonomous trading bot.
You have access to the Alpaca MCP to research stocks and markets.

STRATEGY JOURNAL (last 20 entries):
{journal_content}

CURRENT PORTFOLIO:
{portfolio_state}

CURRENT WATCHLIST:
{watchlist_with_theme_tags}

PERFORMANCE BY THEME:
{theme_performance_breakdown}

OUR MACRO THEMES:
1. AI/Automation — {weight}%
2. Climate Transition — {weight}%
3. Aging Populations — {weight}%
4. Wealth Inequality — {weight}%
{any_discovered_themes}

INVESTMENT GOAL: 20%+ annual returns, high risk tolerance,
swing trading (hold days to weeks). We can go long AND short.

YOUR TASKS:
1. Use the Alpaca tools to research current market conditions
2. Evaluate how our themes are performing against broader markets
3. Research 2-3 potential new stocks that align with our themes
4. Recommend watchlist changes (add/remove)
5. Set strategic direction for the coming week
6. Update theme weights if needed

Respond with JSON:
{
  "market_analysis": "...",
  "theme_performance": {...},
  "watchlist_adds": [{"ticker": "AVGO", "theme": "ai_automation", "reason": "..."}],
  "watchlist_removes": [{"ticker": "XOM", "reason": "..."}],
  "theme_weight_changes": [{"theme": "...", "old_weight": 0.25, "new_weight": 0.30}],
  "weekly_direction": "...",
  "journal_entry": "..."
}
```

#### Safety Bounds

| Rule | Limit | Why |
|------|-------|-----|
| Max stocks added per week | 5 | Prevent over-diversification |
| Max stocks removed per week | 3 | Don't gut the watchlist |
| Min watchlist size | 5 | Always have enough to trade |
| Max watchlist size | 30 | Keep analysis manageable |
| Theme weight bounds | 10-40% each | No single theme dominates |
| Max budget per review | $1.00 | Cost control |

---

### 3. Enhanced Daily Tactical Review

**File:** `src/adaptation/optimizer.py` (updated)

The existing daily review, now enhanced with journal context:

**What's new:**
- Receives the strategy journal as part of the prompt (knows previous decisions)
- Can flag specific stocks to avoid or prioritize today
- Tracks whether previous parameter changes helped or hurt
- Operates within the strategic framework set by the weekly review

**What stays the same:**
- Adjusts numeric parameters (sentiment thresholds, RSI levels, ATR multipliers)
- Changes capped at 20% per day
- No MCP (speed matters — keep these fast)
- Runs at market close daily

---

### 4. Short Selling

The bot can now profit from negative sentiment instead of just avoiding bad stocks.

#### Signal Generation

**SHORT signal triggers** (in `src/strategy/signals.py`):
- Strong negative sentiment (< -0.6) on recent news
- RSI is overbought (> 70) — stock is overextended
- Downtrend confirmed: SMA-20 below SMA-50
- **MACD bearish crossover** (new) — momentum turning negative
- **Price near upper Bollinger Band** (new) — overextended

**Confidence scoring** mirrors long signals but inverted:
- Stronger negative sentiment → higher confidence
- More overbought RSI → higher confidence
- Theme alignment still applies (e.g., shorting a stock in a weak theme = confidence boost)

#### Risk Management

**Short-specific rules** (in `src/strategy/risk.py`):

| Rule | Value | Why |
|------|-------|-----|
| Max short position size | 10% of portfolio | Same as longs — aggressive |
| Max total short exposure | 30% of portfolio | Shorts have unlimited loss potential |
| Short stop-loss | ABOVE entry (2x ATR) | Reverse direction |
| Short take-profit | BELOW entry (3x ATR) | Profit when price falls |
| Short daily loss breaker | 5% | Shorts can move fast against you |

#### SimBroker Changes

`src/simulation/sim_broker.py` updates:
- `SimPosition` gets an `is_short: bool` field
- **Short entry:** Cash increases (you receive the sale proceeds)
- **Short exit:** Cash decreases (you buy back shares to return)
- **Short P&L:** `(entry_price - exit_price) * quantity` (profit when price drops)
- **Stop/target checks reversed:** Stop triggered when price goes UP, target when price goes DOWN
- Portfolio value accounts for short liability

#### Broker Changes

`src/execution/broker.py` updates:
- `place_short_bracket_order()` — sells short with stop-loss above and take-profit below
- Alpaca paper trading supports short selling natively

---

### 5. Enhanced Technical Analysis

**File:** `src/analysis/technical.py` (updated)

Two new indicators added to `TechnicalSnapshot`:

#### MACD (Moving Average Convergence Divergence)

Momentum indicator that shows trend direction and strength.

- **MACD line:** EMA-12 minus EMA-26
- **Signal line:** EMA-9 of MACD line
- **Histogram:** MACD minus Signal (positive = bullish momentum)

**Usage in signals:**
- MACD crosses above signal → bullish confirmation (boost buy confidence)
- MACD crosses below signal → bearish confirmation (boost short confidence)
- Histogram growing → momentum strengthening

**Library:** `ta.trend.MACD`

#### Bollinger Bands

Volatility bands that identify overbought/oversold conditions.

- **Upper band:** SMA-20 + (2 x standard deviation)
- **Middle band:** SMA-20
- **Lower band:** SMA-20 - (2 x standard deviation)

**Usage in signals:**
- Price near lower band → oversold, boost buy confidence
- Price near upper band → overextended, boost short confidence
- **Squeeze detection:** When bands narrow, a breakout is imminent — increase position sizing on next signal

**Library:** `ta.volatility.BollingerBands`

#### Updated TechnicalSnapshot

```python
@dataclass
class TechnicalSnapshot:
    ticker: str
    # Existing
    rsi: float | None
    sma_20: float | None
    sma_50: float | None
    atr: float | None
    volume: float | None
    avg_volume: float | None
    current_price: float | None

    # New — MACD
    macd_line: float | None
    macd_signal: float | None
    macd_histogram: float | None

    # New — Bollinger Bands
    bb_upper: float | None
    bb_middle: float | None
    bb_lower: float | None
    bb_width: float | None       # Band width (volatility measure)

    # Computed properties
    @property
    def is_macd_bullish(self) -> bool: ...
    @property
    def is_macd_bearish(self) -> bool: ...
    @property
    def is_near_lower_band(self) -> bool: ...
    @property
    def is_near_upper_band(self) -> bool: ...
    @property
    def is_bb_squeeze(self) -> bool: ...
```

---

## Updated Config

```yaml
# config/default.yaml — new/changed sections

trading:
  # ... existing params ...

  # Short selling
  enable_short_selling: true
  short_sentiment_threshold: -0.6
  max_short_exposure_pct: 0.30
  max_short_position_pct: 0.10
  short_daily_loss_breaker_pct: 0.05

  # Enhanced technicals
  macd_fast: 12
  macd_slow: 26
  macd_signal: 9
  bollinger_window: 20
  bollinger_std: 2

adaptation:
  # ... existing params ...

  # Journal
  journal_path: "data/strategy_journal.md"
  journal_max_entries: 20

  # Weekly review
  weekly_review_enabled: true
  weekly_review_use_mcp: true
  weekly_max_budget_usd: 1.00
  weekly_max_watchlist_adds: 5
  weekly_max_watchlist_removes: 3
  min_watchlist_size: 5
  max_watchlist_size: 30

  # Theme weights (initial — Claude adjusts over time)
  theme_weights:
    ai_automation: 0.30
    climate_transition: 0.25
    aging_populations: 0.25
    wealth_inequality: 0.20
```

---

## Updated Project Structure

```
trading-bot/
├── src/
│   ├── data/
│   │   ├── market.py              # Alpaca price/account data
│   │   ├── news.py                # News aggregation
│   │   └── watchlist.py           # Watchlist management
│   │
│   ├── analysis/
│   │   ├── sentiment.py           # FinBERT sentiment scoring
│   │   └── technical.py           # RSI, SMA, ATR, MACD, Bollinger Bands
│   │
│   ├── strategy/
│   │   ├── signals.py             # Long + short signal generation
│   │   ├── risk.py                # Position sizing + short exposure rules
│   │   └── themes.py              # Theme management + classification
│   │
│   ├── execution/
│   │   └── broker.py              # Alpaca long + short order execution
│   │
│   ├── adaptation/
│   │   ├── optimizer.py           # Daily tactical review (with journal)
│   │   ├── journal.py             # Strategy journal read/write/truncate
│   │   └── weekly_review.py       # Weekly strategic review + stock discovery
│   │
│   ├── simulation/
│   │   ├── engine.py              # Simulation engine (shorts + weekly reviews)
│   │   ├── sim_broker.py          # Simulated broker (long + short)
│   │   ├── report.py              # Report generator
│   │   └── run_sim.py             # CLI runner
│   │
│   ├── storage/
│   │   ├── database.py            # SQLite operations
│   │   └── models.py              # Data classes
│   │
│   ├── bot.py                     # Main trading loop
│   └── main.py                    # Entry point + scheduler
│
├── data/
│   ├── trading.db                 # SQLite database
│   └── strategy_journal.md        # Persistent strategy journal
│
├── config/
│   └── default.yaml               # All configurable parameters
│
├── tests/
│   └── ...
│
├── ARCHITECTURE.md                # V1 architecture (reference)
├── ARCHITECTURE_V2.md             # This file
└── CLAUDE.md                      # Project-specific Claude instructions
```

---

## File Changes Summary

| File | Change | What |
|------|--------|------|
| `src/adaptation/journal.py` | **NEW** | Strategy journal management (read/write/truncate) |
| `src/adaptation/weekly_review.py` | **NEW** | Weekly strategic review with Alpaca MCP |
| `src/adaptation/optimizer.py` | MODIFY | Add journal context to daily review prompts |
| `src/analysis/technical.py` | MODIFY | Add MACD + Bollinger Bands to TechnicalSnapshot |
| `src/strategy/signals.py` | MODIFY | Short signals + MACD/BB confidence boosters |
| `src/strategy/risk.py` | MODIFY | Short position rules, exposure limits |
| `src/simulation/sim_broker.py` | MODIFY | Short position tracking, reversed P&L |
| `src/execution/broker.py` | MODIFY | Short bracket orders via Alpaca |
| `src/simulation/engine.py` | MODIFY | Wire weekly reviews + short selling |
| `src/bot.py` | MODIFY | Short selling in live trading loop |
| `src/main.py` | MODIFY | Weekly review in scheduler |
| `config/default.yaml` | MODIFY | New parameters |
| Tests for all above | **NEW** | Unit tests for every new/changed module |

---

## Implementation Order

### Phase 7A — Strategy Journal + Context Persistence
1. Build `src/adaptation/journal.py` (read, write, truncate)
2. Update `src/adaptation/optimizer.py` to include journal in prompts
3. Tests for journal management

### Phase 7B — Enhanced Technicals
1. Add MACD + Bollinger Bands to `src/analysis/technical.py`
2. Add computed properties to `TechnicalSnapshot`
3. Update `src/strategy/signals.py` to use new indicators in confidence
4. Tests for new indicators + signal changes

### Phase 7C — Short Selling
1. Update `src/storage/models.py` (side enum if needed)
2. Update `src/simulation/sim_broker.py` for short positions
3. Update `src/strategy/signals.py` for short signal generation
4. Update `src/strategy/risk.py` for short exposure rules
5. Update `src/execution/broker.py` for short orders
6. Update `src/simulation/engine.py` to process short signals
7. Tests for all short selling logic

### Phase 7D — Weekly Strategic Review
1. Install uv + configure Alpaca MCP
2. Build `src/adaptation/weekly_review.py`
3. Wire into `src/simulation/engine.py`
4. Wire into `src/main.py` scheduler
5. Update `config/default.yaml` with weekly review params
6. Tests for weekly review

### Phase 7E — Integration Testing
1. Run Q1 2024 simulation: dry run vs V2 adapted
2. Compare results against V1
3. Tune and iterate

---

## Design Philosophy

**Claude as strategist, not trader.** Claude sets direction — which stocks to watch, what themes to favor, how to tune parameters. The rules-based engine executes trades. This keeps execution predictable, auditable, and fast.

**Theme-first, technicals for timing.** Stock selection is driven by macro themes and news sentiment. Technical indicators confirm the right moment to enter and exit. We don't try to out-analyze hedge fund quant desks on chart patterns.

**Aggressive but bounded.** High risk tolerance — shorts enabled, aggressive sizing, willing to concentrate in high-conviction themes. But hard circuit breakers prevent catastrophic losses. No single bad week should end the game.

**Context is king.** The strategy journal gives Claude institutional memory. Each review builds on the last. Over time, the journal becomes a rich history of what works in which market conditions — something no stateless system can match.

**Explore to discover.** The Alpaca MCP lets Claude go beyond what we pre-program. If Claude notices a trend we didn't anticipate, it can research stocks, validate data, and act on it. This is the edge — human-level reasoning with machine-speed execution.
