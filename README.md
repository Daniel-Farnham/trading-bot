# Druckenmiller-Style Trading Bot

An AI-powered trading bot where Claude acts as CIO — identifying macro regime changes, sizing big on highest-conviction ideas, and holding winners for months. Supports equities and options via Alpaca paper/live API.

## Architecture

Two execution environments sharing core strategy components:

```
SIMULATION (backtest)              LIVE (paper / real money)
─────────────────────              ─────────────────────────
src/simulation/                    src/live/
  thesis_sim.py  (sim loop)          orchestrator.py  (Call 1 + triggers + Call 3)
  sim_broker.py  (in-memory)         executor.py      (real Alpaca orders)
  report.py      (perf report)       scheduler.py     (APScheduler cron)
                                     claude_client.py (Anthropic SDK + spend caps)
                                     trigger_check.py (volatility monitor)
                                     research_tools.py (Claude's exploration tools)
                                     notifier.py      (Gmail alerts)
                                     health.py        (web dashboard)

SHARED
──────
src/strategy/    decision_engine, thesis_manager, risk_v3, contract_selector
src/analysis/    technical indicators (RSI, MACD, OBV, ADX, ATR, Bollinger, HV)
src/research/    news_client (Alpaca), fundamentals (yfinance), world_state
src/execution/   broker (equities), options_broker (options)
src/data/        market data, options data (Alpaca)
src/options/     Black-Scholes pricing (sim), live pricing via Alpaca (live)
```

## Live Trading — Two-Call Architecture

### Call 1: Discovery (Daily, 9:00 AM ET)
- Pre-fetches overnight news + holdings-specific headlines from Alpaca
- Claude has **research tools** to dig deeper: `search_news`, `get_fundamentals`, `get_price_action`, `get_technicals`, `screen_by_theme`
- Discovers opportunities beyond the known universe
- Expands the universe (max 150 stocks), updates watchlist (max 20)
- Produces daily macro assessment

### Trigger Check (Every 30 min, 9:30 AM - 3:00 PM ET)
- No Claude call — pure Alpaca data + arithmetic
- Monitors holdings + watchlist for: intraday shocks (>3x ATR), volatility drift (>5% swing), low volatility (SPY HV < 30th percentile)
- Zero cooldown — fires Call 3 immediately if triggered

### Call 3: Decision & Execution (Friday 3:30 PM + on trigger)
- Self-sufficient — fetches technicals + fundamentals for full universe from live Alpaca bars
- Same proven prompt structure as the sim
- Receives Call 1 output as additional context
- Executes trades via Alpaca (OPG orders if market closed)
- Memory only written for trades that actually execute

## Strategy: Two-Tier Position Management

| Tier | Confidence | Max Allocation | Stops | Exit |
|------|-----------|---------------|-------|------|
| **Scout** | low / medium | 5% / 8% | Mechanical (Claude's stop) | Auto at stop/target |
| **Core** | high / highest | Uncapped | 30% catastrophic safety net | Claude thesis review |

- Max 8 positions (prefer 3-5 concentrated bets)
- Pyramiding into winners (re-submit ticker with higher allocation)
- Scout to Core upgrade path
- Stopped-out scouts move to WATCHING (6-review expiry)
- Options: BUY_CALL, BUY_PUT, SELL_PUT with real Alpaca options data

## Stock Universe

~95 stocks across 9 themes in `config/default.yaml`:

- **AI Technology** (15) — NVDA, AVGO, AMD, MSFT, GOOGL, AMZN, META, etc.
- **Healthcare/Aging** (10) — LLY, NVO, UNH, JNJ, ISRG, etc.
- **Energy/Climate** (9) — XOM, CVX, CEG, VST, NEE, etc.
- **Data Center Infrastructure** (6) — VRT, EQIX, DLR, PWR, EME, ETN
- **Finance** (9) — JPM, GS, V, MA, COIN, etc.
- **Consumer** (8) — COST, WMT, NKE, CMG, etc.
- **Biotech** (5) — NTRA, INSM, GILD, MRNA, etc.
- **Industrials/Defense** (4) — GE, RTX, LMT, CAT
- **Discovery Pool** (25+) — Broader market for shorts and discovery

Call 1 can expand the universe up to 150 stocks. Claude manages removals when at cap.

## Memory System

Eight persistent markdown files give Claude continuity:

| File | Purpose | Updated |
|------|---------|---------|
| `active_theses.md` | Current investment theses | Call 3 |
| `portfolio_ledger.md` | Current positions + P&L | Call 3 |
| `world_view.md` | Macro regime + forward outlook | Call 1 (observation) + monthly (full) |
| `themes.md` | Scored investment themes (1-5) | Call 3 |
| `lessons_learned.md` | Rules learned from experience | Call 3 |
| `beliefs.md` | Durable cross-regime principles | Monthly |
| `quarterly_summaries.md` | Compressed performance history | Monthly |
| `decision_journal.md` | Why each trade was made | Call 3 |

## Monitoring

### Web Dashboard
Deployed on Railway at your service URL. Tabs:
- **Overview** — status, last call times, portfolio value vs SPY
- **Portfolio** — live Alpaca positions with P&L
- **Watchlist** — watchlist + universe ticker counts
- **Claude Output** — full Call 1 and Call 3 JSON outputs
- **Memory** — all md files
- **API Spend** — token usage and costs
- **Logs** — last 200 log entries

Manual **Run Call 1** and **Run Call 3** buttons on the dashboard.

### Email Notifications
- After every Call 1 and Call 3 with full logs
- EOD portfolio update (no Claude) with md files attached
- Alert emails for triggers, budget exceeded, errors

### Local CLI
```bash
python -m src.live.status_cli              # Overview
python -m src.live.status_cli portfolio    # Live Alpaca positions
python -m src.live.status_cli watchlist    # Current watchlist
python -m src.live.status_cli universe    # Universe grouped by source
python -m src.live.status_cli call1       # Last Call 1 output
python -m src.live.status_cli call3       # Last Call 3 output
python -m src.live.status_cli memory      # All memory files
python -m src.live.status_cli spend       # API spend log
```

## Running the Live Bot

### Local
```bash
# Set up .env with API keys (see .env.example)
python -m src.live.main
```

### Railway Deployment
1. Connect GitHub repo to Railway
2. Set environment variables: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`
3. Create persistent volume at `/app/data/live`
4. Deploy — Railway auto-deploys from `main` on every push

Set `FORCE_FIRST_BOOT=true` to wipe state and re-initialize (remove after).

## Running Simulations

```bash
python -m src.simulation.run_thesis_sim \
  --start 2025-06-01 --end 2025-11-30 \
  --review-cadence 7 \
  --output data/reports/bull_2025h2.json
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | required | Sim start date (YYYY-MM-DD) |
| `--end` | required | Sim end date (YYYY-MM-DD) |
| `--cash` | 100,000 | Initial capital |
| `--review-cadence` | 5 | Trading days between reviews |
| `--data-dir` | data/v3_sim | Memory file directory |
| `--output` | None | Save JSON report to file |

## Cost Estimate

| Call | Frequency | Cost |
|------|-----------|------|
| Call 1 | Daily (with tool use) | ~$0.05-0.15 |
| Trigger check | Every 30 min (no Claude) | $0.00 |
| Call 3 | Weekly + on trigger | ~$0.09 |
| **Monthly** | | **~$2-5** |

Hard caps: $2/day, $40/month.

## Requirements

- Python 3.9+
- Alpaca API keys (paper or live)
- Anthropic API key
- Gmail app password (for notifications)

```bash
pip install -r requirements.txt
pytest tests/ -q
```

## Simulation Results

| Period | Type | Return | SPY | Alpha | Win Rate | Trades |
|--------|------|--------|-----|-------|----------|--------|
| 2024 Apr-2025 Aug (17mo) | Full cycle | +25.7% | +17.7% annualized | +10%+ | — | — |
| 2025 Jun-Nov | Bull | +17.7% | +16.3% | +1.4% | 82.6% | 23 |
| 2022 Jan-Jun | Bear | +5.8% | -20.1% | +26.0% | 47.6% | 21 |
