# Fine-Tuning Approach

## Goal

Iteratively improve Claude's investment decision quality through simulation runs, prompt engineering, and architectural changes — without touching model weights. This is behavioural tuning via prompt, context, and memory design.

## What We're Tuning

### 1. Prompt Design (decision_engine.py)

The prompt is the primary lever. Each iteration refines what information Claude receives and how it's framed.

**Key prompt sections:**
- Portfolio state with SPY benchmark comparison (creates urgency to deploy capital)
- Memory context (theses, ledger, lessons, themes — Claude's persistent state)
- World state (date-filtered news from Alpaca)
- Technical timing data (RSI, MACD, SMA50, Bollinger Bands)
- Stock universe with themed groups + unbiased discovery pool
- Goals: 20% annualized target + beat S&P 500
- Rules: position limits, cash reserves, shorting guidance
- JSON schema with both LONG and SHORT examples

**What we've learned:**
- Claude follows JSON examples closely — if you only show a LONG example, it only goes long
- Explicit benchmark comparison in portfolio state reduces cash drag
- "You SHOULD actively consider shorts" works better than "You CAN short"
- Labelling discovery pool as "Broader Market" (not "short candidates") prevents bias

### 2. Memory Architecture (thesis_manager.py)

Six markdown files give Claude continuity across stateless CLI calls. The design of these files directly affects decision quality.

**Tuning decisions:**
- Lessons are uncapped within a sim — quality over token efficiency
- Themes scored 1-5 with auto-removal at 1 — lets Claude evolve its worldview
- Themes reset between sim runs — prevents context leak across different market regimes
- Simulation log excluded from Claude's context — our reference only, no context leak
- Quarterly summaries capped at 8 — compressed history, not full replay

### 3. Simulation Parameters

**Review cadence:** 7 trading days (weekly). Shorter = more Claude calls + cost. Longer = slower reaction.

**Delayed first review:** Bot observes one review cycle before deploying capital. Prevents blind day-one allocation.

**Take-profit levels:** Longs at 2x entry, shorts at 0.5x entry. Wide placeholders — Claude decides actual exits via thesis reviews.

**Catastrophic stops:** 18% wide. These are emergency exits, not trading stops.

## Iteration History

### V3.0 — Initial Implementation
- Claude makes thesis-driven decisions via CLI
- 5-stock watchlist, Tiingo news (broken date filtering)
- Result: +5-6% bull, long-only, very conservative

### V3.1 — Curated Universe + Alpaca News
- Replaced broken Tiingo with Alpaca News API (proper date filtering)
- Expanded to 57-stock themed universe
- Fixed portfolio value to reflect unrealized P&L
- Result: Similar returns but better news quality

### V3.2 — Discovery Pool + Benchmarking + Shorts Fix
- Added 20 discovery pool stocks (broader market, natural short candidates)
- SPY benchmark tracking in every review (Claude sees how it's doing vs market)
- Fixed critical short take-profit bug (was setting TP above entry, instant exit)
- Updated goals: 20% annualized + beat S&P 500
- Delayed first review (observe before deploying)
- Added SHORT example to JSON schema
- Stronger shorting language in prompt
- Result: +17.7% bull (+1.4% alpha), +5.8% bear (+26% alpha)

## Tuning Methodology

### 1. Run Simulation
Pick a market regime (bull, bear, transition) and run a sim:
```bash
python -m src.simulation.run_thesis_sim \
  --start YYYY-MM-DD --end YYYY-MM-DD \
  --review-cadence 7 \
  --output data/reports/run_name.json
```

### 2. Analyse Results
- Check `.txt` report for lessons learned, per-ticker P&L, active theses
- Compare return vs SPY (alpha)
- Look at simulation_log.md for cross-run trends
- Check themes.md for theme evolution

### 3. Identify Issues
Common patterns:
- **Too conservative (cash drag):** Bot sits in cash during rallies → strengthen deployment language, show benchmark gap
- **No shorts:** Bot ignores short opportunities → add SHORT examples to JSON, strengthen language
- **Stop cascade:** Multiple stops hit same day → review stop width, consider staggering
- **Same 3 stocks:** Bot only trades favourites → expand universe, add discovery pool
- **Lessons bloat:** Too many lessons → currently uncapped, may need summarisation for very long runs

### 4. Make Changes
- Prompt wording in `decision_engine.py`
- Memory structure in `thesis_manager.py`
- Sim parameters in `thesis_sim.py`
- Universe composition in `config/default.yaml`

### 5. Re-run and Compare
Same period, same initial conditions. Compare alpha, win rate, drawdown, trade count. Log results to simulation_log.md.

## Key Metrics

| Metric | Target | Why |
|--------|--------|-----|
| Alpha vs SPY | > 0% | Must beat passive investing |
| Annualized Return | > 20% | Justify the complexity |
| Max Drawdown | < 15% | Capital preservation |
| Win Rate | > 50% | More winners than losers |
| Trade Count | 15-30 per 6 months | Active but not churning |
| Short Usage | 2-5 shorts in bear markets | Hedging, not just long-only |

## Test Periods

| Period | Type | SPY Return | Purpose |
|--------|------|-----------|---------|
| 2025-06-01 to 2025-11-30 | Bull | +16.3% | Can we beat the market in a rally? |
| 2022-01-01 to 2022-06-30 | Bear | -20.1% | Can we preserve capital + short? |
| 2021-11-01 to 2022-02-28 | Transition | ~-8% | Can we detect regime change? |
| 2023-01-01 to 2023-12-31 | Recovery | +24% | Can we ride a recovery? |

## What's NOT Being Tuned

- Model weights — we use Claude Sonnet as-is via CLI
- Technical indicators — RSI/MACD/SMA50/BB are standard
- Risk manager rules — position limits, stops are fixed constraints
- News source — Alpaca News API, not configurable per run

## Future Tuning Ideas

- Compile best lessons from all sim runs into a seed file for live trading
- Filter technicals to top 20 most interesting tickers (reduce prompt size)
- Monthly theme reviews only (currently every review)
- Sector rotation signals from cross-ticker technical patterns
- Position sizing based on theme conviction scores
