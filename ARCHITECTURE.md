# Trading Bot — Architecture Overview

## Vision

An autonomous, news-sentiment-driven swing trading bot that trades US stocks via Alpaca paper trading. The bot reads company news, scores sentiment, overlays basic technical analysis, and executes trades without human intervention. It adapts over time by reviewing its own performance and adjusting strategy parameters.

**Target:** 20%+ annual returns | **Style:** Swing trading (hold days to weeks) | **Risk:** High tolerance

---

## High-Level Flow

```
┌─────────────┐     ┌──────────────┐     ┌────────────────┐     ┌────────────┐
│  SCHEDULER   │────▶│  DATA LAYER  │────▶│ STRATEGY ENGINE │────▶│  EXECUTOR  │
│ (every 30m)  │     │              │     │                │     │            │
│              │     │ • News feed  │     │ • Sentiment    │     │ • Alpaca   │
│              │     │ • Prices     │     │ • Technicals   │     │ • Orders   │
│              │     │ • Portfolio  │     │ • Signals      │     │ • Stops    │
└─────────────┘     └──────────────┘     │ • Risk mgmt    │     └─────┬──────┘
                                         └────────────────┘           │
                                                                      │
┌─────────────┐     ┌──────────────┐                                  │
│   ADAPTER    │◀────│  TRADE LOG   │◀─────────────────────────────────┘
│              │     │              │
│ • Review     │     │ • SQLite DB  │
│ • Learn      │     │ • Every trade│
│ • Adjust     │     │ • P&L track  │
└─────────────┘     └──────────────┘
```

### The Loop (runs every 30 minutes during market hours)

1. **Collect** — Pull latest news for watchlist stocks + current prices + portfolio state
2. **Analyze** — Score news sentiment (FinBERT) + check technical indicators (RSI, moving averages, volume)
3. **Decide** — Generate buy/sell/hold signals by combining sentiment + technicals
4. **Risk Check** — Validate against position limits, portfolio exposure, max drawdown rules
5. **Execute** — Place orders via Alpaca paper trading API
6. **Log** — Record everything to SQLite (trade, reasoning, sentiment scores, outcome)
7. **Adapt** — Daily review: Claude API analyzes recent trades, suggests parameter tweaks

---

## Architecture Layers

### 1. Data Layer

Responsible for ingesting all external data the bot needs to make decisions.

#### Market Data (Alpaca API)
- **Real-time prices** via Alpaca's data API (free tier = IEX exchange)
- **Historical bars** for technical indicator calculation (daily + hourly)
- **Account & positions** to know current portfolio state
- SDK: `alpaca-py` — the official Python SDK

#### News Feed
- **Primary:** Alpaca News API — provides real-time news for any ticker, included with free account
- **Secondary:** Tiingo News API — broader coverage, free tier (500 req/day)
- **Fallback:** RSS feeds from major financial outlets (Reuters, Bloomberg summaries)

The bot maintains a **watchlist** of ~20-50 stocks (configurable). On each cycle, it pulls news published since the last check.

```
src/data/
├── market.py        # Price data, account info, positions
├── news.py          # News aggregation from multiple sources
└── watchlist.py     # Watchlist management (add/remove tickers)
```

---

### 2. Analysis Layer

Transforms raw data into actionable signals.

#### Sentiment Analysis
- **FinBERT** (HuggingFace `ProsusAI/finbert`) — a BERT model fine-tuned on financial text
- Each news article/headline gets scored: `positive`, `negative`, `neutral` + confidence
- Aggregate sentiment per ticker over a rolling window (e.g., last 24 hours)
- **Why FinBERT over Claude API?** — It's free, fast, runs locally, and is specifically trained for financial text. We reserve Claude API calls for higher-level strategic decisions where nuance matters.

#### Technical Analysis
Lightweight technical overlay — not the primary signal, but used for **timing** and **confirmation**.

| Indicator | Purpose |
|-----------|---------|
| RSI (14-period) | Overbought/oversold filter — avoid buying into overbought stocks |
| SMA 20/50 crossover | Trend direction — only trade with the trend |
| Volume spike detection | Confirms news is being acted on by the market |
| ATR (Average True Range) | Sets stop-loss and take-profit distances dynamically |

Library: `pandas-ta` — comprehensive, well-maintained, pure Python.

```
src/analysis/
├── sentiment.py     # FinBERT scoring + aggregation
└── technical.py     # RSI, SMA, volume, ATR calculations
```

---

### 3. Strategy Engine

The decision-making core. Combines sentiment + technicals into trade signals.

#### Signal Generation

A signal is generated when:

**BUY signal:**
- Sentiment score > threshold (e.g., > 0.6 positive) on recent news
- RSI is NOT overbought (< 70)
- Price is above SMA-50 (uptrend) OR strong positive sentiment overrides
- Volume confirms (above average)

**SELL signal (exit existing position):**
- Sentiment turns negative (< -0.4) on new news
- Stop-loss hit (set at 2x ATR below entry)
- Take-profit hit (set at 3x ATR above entry — 1.5:1 reward/risk)
- Max hold time exceeded (e.g., 10 trading days with no movement)

**Confidence scoring:** Each signal gets a confidence score (0-1) based on how many factors align. Higher confidence = larger position size.

#### Risk Management

Hard rules that cannot be overridden:

| Rule | Value | Why |
|------|-------|-----|
| Max position size | 10% of portfolio | No single stock can wreck the portfolio |
| Max open positions | 10 | Diversification |
| Max daily loss | 3% of portfolio | Circuit breaker — stop trading for the day |
| Max drawdown | 15% of portfolio | Circuit breaker — pause and trigger strategy review |
| Min cash reserve | 20% of portfolio | Always have dry powder |

```
src/strategy/
├── signals.py       # Signal generation logic
└── risk.py          # Position sizing + risk rules
```

---

### 4. Execution Layer

Translates signals into actual orders on Alpaca.

- **Order types:** Primarily limit orders (not market) to control entry price
- **Bracket orders:** Entry + stop-loss + take-profit submitted as a single bracket via Alpaca
- **Idempotency:** Each signal gets a unique ID to prevent duplicate orders
- **Rate limiting:** Respects Alpaca API limits

```
src/execution/
└── broker.py        # Alpaca order management
```

---

### 5. Storage & Logging

SQLite database (simple, no server needed, file-based).

#### Tables

**trades**
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT | Unique trade ID |
| ticker | TEXT | Stock symbol |
| side | TEXT | buy/sell |
| quantity | INT | Shares |
| entry_price | REAL | Fill price |
| exit_price | REAL | Fill price (null if open) |
| stop_loss | REAL | Stop-loss price |
| take_profit | REAL | Take-profit target |
| sentiment_score | REAL | Sentiment at time of trade |
| confidence | REAL | Signal confidence score |
| reasoning | TEXT | Why the trade was made |
| status | TEXT | open/closed/stopped_out |
| pnl | REAL | Realized P&L |
| opened_at | TEXT | Timestamp |
| closed_at | TEXT | Timestamp |

**sentiment_log**
| Column | Type | Description |
|--------|------|-------------|
| ticker | TEXT | Stock symbol |
| headline | TEXT | News headline |
| source | TEXT | News source |
| score | REAL | FinBERT sentiment score |
| timestamp | TEXT | When the news was published |

**strategy_params**
| Column | Type | Description |
|--------|------|-------------|
| key | TEXT | Parameter name |
| value | REAL | Current value |
| updated_at | TEXT | Last modified |
| updated_by | TEXT | "system" or "claude_review" |

```
src/storage/
├── database.py      # SQLite operations
└── models.py        # Data classes for trades, signals, etc.
```

---

### 6. Adaptation Layer

What makes this bot "learn" rather than just follow static rules.

#### Daily Performance Review (Claude Code CLI)

Uses `claude -p` (non-interactive mode) — no extra API costs, uses your existing Claude Code subscription.

At market close each day, the bot:
1. Compiles today's trades + outcomes
2. Pulls the last 7 days of trade history
3. Calls Claude Code via subprocess:

```python
result = subprocess.run([
    "claude", "-p", prompt,
    "--output-format", "json",
    "--max-budget-usd", "0.50",
], capture_output=True, text=True)
```

4. Claude responds with suggested parameter adjustments
5. Bot applies changes within safety bounds (no parameter can change more than 20% per day)
6. Changes are logged to `strategy_params` table with reasoning

#### Win/Loss Pattern Tracking
- Track win rate by: sector, sentiment score range, time of day, hold duration
- Automatically reduce position size for underperforming patterns
- Increase position size for consistently profitable patterns

```
src/adaptation/
└── optimizer.py     # Performance review + parameter adjustment
```

---

### 7. Scheduler

Orchestrates the entire loop.

- **APScheduler** (Python library) for cron-like scheduling
- Runs the main loop every 30 minutes during market hours (9:30 AM - 4:00 PM ET)
- Runs daily review at 4:30 PM ET
- Runs weekly deep review on Saturdays (more comprehensive Claude analysis)
- Handles market holidays (skips non-trading days)

```
src/
├── main.py          # Entry point + scheduler setup
└── bot.py           # The main trading loop orchestration
```

---

## Project Structure

```
trading-bot/
├── src/
│   ├── data/
│   │   ├── __init__.py
│   │   ├── market.py          # Alpaca price/account data
│   │   ├── news.py            # News aggregation
│   │   └── watchlist.py       # Watchlist management
│   │
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── sentiment.py       # FinBERT sentiment scoring
│   │   └── technical.py       # RSI, SMA, volume, ATR
│   │
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── signals.py         # Signal generation
│   │   └── risk.py            # Position sizing + risk rules
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   └── broker.py          # Alpaca order execution
│   │
│   ├── adaptation/
│   │   ├── __init__.py
│   │   └── optimizer.py       # Claude-powered strategy review
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── database.py        # SQLite operations
│   │   └── models.py          # Data classes
│   │
│   ├── bot.py                 # Main trading loop
│   └── main.py                # Entry point + scheduler
│
├── tests/
│   └── ...
│
├── config/
│   └── default.yaml           # All configurable parameters
│
├── data/
│   └── trading.db             # SQLite database (created at runtime)
│
├── .env                       # API keys (never committed)
├── .gitignore
├── requirements.txt
├── ARCHITECTURE.md            # This file
└── CLAUDE.md                  # Project-specific Claude instructions
```

---

## Tech Stack

| Component | Tool | Why |
|-----------|------|-----|
| Language | Python 3.12+ | Best ecosystem for trading/ML |
| Broker API | `alpaca-py` | Official SDK, paper + live, free |
| Sentiment | `transformers` + FinBERT | Free, fast, finance-specific |
| Technical Analysis | `pandas-ta` | Comprehensive, maintained |
| Data | `pandas` | Industry standard for tabular data |
| Scheduling | `APScheduler` | Lightweight, cron-like, reliable |
| Database | `sqlite3` (stdlib) | Zero setup, file-based |
| Config | `pyyaml` | Human-readable config files |
| AI Advisor | `claude -p` (CLI) | Claude Code for strategy adaptation — no extra cost |
| HTTP | `httpx` | Modern async HTTP client |

---

## API Keys Needed

| Service | Purpose | Cost |
|---------|---------|------|
| **Alpaca** | Paper trading + market data | Free |
| **Claude Code** | Daily strategy review via `claude -p` | $0 (included in subscription) |
| **Tiingo** (optional) | Broader news coverage | Free tier (500 req/day) |

Total estimated monthly cost: **$5-15**

---

## Implementation Order

Building this in phases so we have something working quickly:

### Phase 1 — Foundation (Days 1-2)
- [ ] Project setup (virtualenv, dependencies, config)
- [ ] Alpaca connection (paper account, fetch prices, fetch account)
- [ ] SQLite database schema + basic operations
- [ ] Simple watchlist (hardcoded top 20 S&P 500 stocks)

### Phase 2 — Analysis (Days 3-4)
- [ ] News fetching from Alpaca News API
- [ ] FinBERT sentiment scoring pipeline
- [ ] Technical indicators (RSI, SMA, volume, ATR)

### Phase 3 — Strategy + Execution (Days 5-7)
- [ ] Signal generation logic
- [ ] Risk management rules
- [ ] Alpaca order execution (bracket orders)
- [ ] The main trading loop

### Phase 4 — Automation (Days 8-9)
- [ ] APScheduler integration
- [ ] Trade logging to SQLite
- [ ] Error handling + retry logic

### Phase 5 — Adaptation (Days 10-12)
- [ ] Performance tracking queries
- [ ] Claude API daily review integration
- [ ] Parameter auto-adjustment with safety bounds

### Phase 6 — Polish (Days 13-14)
- [ ] CLI dashboard (simple terminal UI showing positions, P&L, recent trades)
- [ ] Alerts (optional: Slack/Discord webhook on trades)
- [ ] Testing + hardening

---

## Key Design Decisions

**Why not MCPs for the autonomous bot?**
MCPs are great for interactive use (Claude Desktop), but an autonomous bot should call APIs directly. Fewer moving parts, easier to debug, no dependency on Claude being "in the loop" for every trade. We use Claude API specifically for the adaptation layer — the one place where LLM reasoning adds clear value.

**Why FinBERT over Claude for sentiment?**
Volume. The bot may score hundreds of headlines per day. FinBERT runs locally in milliseconds for free. Claude API is reserved for the daily strategic review where nuance matters and volume is low (1 call/day).

**Why SQLite over Postgres?**
This is a single-user bot on a local machine. SQLite is zero-config, file-based, and more than fast enough. If you ever scale this up, swapping to Postgres is straightforward.

**Why 30-minute cycles for swing trading?**
Swing trading doesn't need millisecond execution. 30 minutes is frequent enough to catch news-driven moves within the trading day, but infrequent enough to keep API usage low and avoid overtrading.

**Why limit orders over market orders?**
Market orders can fill at unexpected prices, especially in volatile stocks. Limit orders ensure we control entry price. The slight risk of a missed fill is worth the price certainty.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| FinBERT misreads sarcasm/complex news | Claude API review catches systematic errors; confidence threshold filters weak signals |
| Alpaca API outage | Retry with exponential backoff; circuit breaker pauses trading after 3 failures |
| Overfitting to recent data | Parameter changes capped at 20% per day; weekly review looks at longer-term trends |
| News arrives too late to act | Alpaca news feed is near-real-time; 30-min cycle is fast enough for swing trades |
| Black swan event (flash crash) | Max daily loss circuit breaker (3%); max drawdown circuit breaker (15%) |
| Strategy never becomes profitable | Track performance from day 1; if consistently losing after 30 days, pause and redesign |
