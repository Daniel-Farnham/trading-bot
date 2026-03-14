# V3 Thesis-Driven Trading Bot

An AI-powered trading bot where Claude makes investment decisions based on real-world news, technical analysis, and evolving investment theses. Positions are held for weeks to quarters — not day-traded.

## How It Works

1. **Research** — Pulls news from Alpaca News API, filtered by date to prevent future-knowledge leakage
2. **Analysis** — Computes technicals (RSI, MACD, SMA50, Bollinger Bands) for ~77 stocks
3. **Decision** — Claude (Sonnet) reviews news + technicals + memory, returns structured JSON with trade decisions
4. **Execution** — Risk manager validates allocations, sim broker (or live Alpaca) executes trades
5. **Memory** — Theses, lessons, portfolio ledger, and themes persist across reviews within a run

## Architecture

```
src/
  strategy/
    decision_engine.py   # Prompt builder + Claude CLI caller
    thesis_manager.py    # 6 persistent markdown memory files
    risk_v3.py           # Allocation-based position sizing + constraints
  research/
    news_client.py       # Alpaca News API client (date-filtered)
    world_state.py       # Formats news into structured brief for Claude
  analysis/
    technical.py         # RSI, MACD, SMA50, Bollinger Bands
  simulation/
    thesis_sim.py        # Weekly-cadence backtesting engine
    sim_broker.py        # In-memory broker (supports long + short)
    run_thesis_sim.py    # CLI entry point
    report.py            # Equity curve CSV export
  data/
    market.py            # Alpaca market data client
  execution/
    broker.py            # Live Alpaca trading client
  storage/
    database.py          # SQLite storage
    models.py            # Data models
config/
  default.yaml           # Universe, trading params, memory paths
```

## Stock Universe

~77 stocks across 6 groups defined in `config/default.yaml`:

- **AI/Technology** (15) — NVDA, AVGO, AMD, MSFT, GOOGL, AMZN, META, etc.
- **Healthcare/Aging** (10) — LLY, NVO, UNH, JNJ, ISRG, etc.
- **Energy/Climate** (9) — XOM, CVX, ENPH, FSLR, NEE, CEG, etc.
- **Finance** (9) — JPM, GS, V, MA, BLK, etc.
- **Consumer/Inequality** (8) — COST, WMT, TGT, NKE, CMG, etc.
- **Discovery Pool** (20) — Broader market stocks for short candidates and discovery (T, BA, PARA, DIS, F, etc.)

Claude can also discover and trade tickers outside this universe via news.

## Memory System

Six persistent markdown files give Claude continuity across stateless CLI calls:

| File | Purpose | Persists Between Runs? |
|------|---------|----------------------|
| `active_theses.md` | Current investment theses (max 20) | No |
| `portfolio_ledger.md` | What we hold right now | No |
| `quarterly_summaries.md` | Compressed performance history | No |
| `lessons_learned.md` | Rules Claude learns from experience | No |
| `themes.md` | Scored investment themes (1-5) | No |
| `simulation_log.md` | Cross-run performance history | Yes |

## Dynamic Themes

Investment themes are scored 1-5 and evolve during reviews:
- New themes start at score **3**
- Claude can adjust scores **+/-1** per review based on news evidence
- Themes at score **1** are auto-removed
- Maximum **8** themes at a time
- Themes are informational — they guide thinking, not allocations

## Running Simulations

```bash
# Bull market (2025 H2)
python -m src.simulation.run_thesis_sim \
  --start 2025-06-01 --end 2025-11-30 \
  --review-cadence 7 \
  --output data/reports/bull_2025h2.json

# Bear market (2022 H1)
python -m src.simulation.run_thesis_sim \
  --start 2022-01-01 --end 2022-06-30 \
  --review-cadence 7 \
  --output data/reports/bear_2022h1.json

# Bull-to-bear transition
python -m src.simulation.run_thesis_sim \
  --start 2021-11-01 --end 2022-02-28 \
  --review-cadence 7 \
  --output data/reports/bull_to_bear.json
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | required | Sim start date (YYYY-MM-DD) |
| `--end` | required | Sim end date (YYYY-MM-DD) |
| `--cash` | 100,000 | Initial capital |
| `--review-cadence` | 5 | Trading days between reviews |
| `--data-dir` | data/v3_sim | Memory file directory |
| `--output` | None | Save JSON report to file |
| `--notes` | "" | Notes for simulation log |
| `-v` | off | Verbose logging |

## Outputs

Each sim produces:
- **Console** — Live progress with portfolio value, SPY benchmark, and trade actions
- **JSON report** — Full results with all trades and review decisions
- **Text report** — Human-readable summary with lessons learned
- **Equity curve CSV** — Daily portfolio snapshots
- **Simulation log** — Appended to `data/simulation_log.md` (persists across runs)

## Key Design Decisions

- **Claude Sonnet** for reviews — fast, cheap, structured JSON output
- **Anti-future-knowledge** — News API date-filtered + explicit prompt guard
- **Delayed first review** — Observes one review cycle before deploying capital
- **Wide catastrophic stops** (18%) — Thesis-driven exits, not tight stop-losses
- **SPY benchmark** — Bot tracks its performance vs S&P 500 every review
- **Long AND short** — Bot can short discovery pool stocks in bear markets
- **On-demand bar downloads** — Discovered tickers get bars downloaded automatically

## Requirements

- Python 3.9+
- Alpaca API keys (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY` in `.env`)
- Claude Code CLI installed

```bash
pip install -r requirements.txt
```

## Tests

```bash
pytest tests/ -q
```

## Simulation Results

| Period | Type | Return | SPY | Alpha | Win Rate | Trades |
|--------|------|--------|-----|-------|----------|--------|
| 2025 Jun–Nov | Bull | +17.7% | +16.3% | +1.4% | 82.6% | 23 |
| 2022 Jan–Jun | Bear | +5.8% | -20.1% | +26.0% | 47.6% | 21 |
