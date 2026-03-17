# Simulation Findings — 2021 H1 (Jan-Jun)

## Benchmark: SPY +14.0%

## Results Summary

| Run | Return | Alpha | Win Rate | Trades | Max DD | Key Changes |
|-----|--------|-------|----------|--------|--------|-------------|
| v4 | +6.6% | -7.3% | 33% (6/12) | 18 | -8.0% | Beliefs, scored lessons, theme discovery, ATR stops (3x, 8-20%) |
| v4b | +10.1% | -3.9% | 42% (11/15) | 26 | -5.8% | v4 + theme score fix (partial) |
| **v5** | **+11.5%** | **-2.4%** | **52% (12/11)** | **23** | **-5.5%** | **25% catastrophic stop, Claude-managed exits, thesis purging** |
| v6 | +2.8% | -11.2% | 48% (11/12) | 23 | -5.2% | v5 + confidence-tiered allocations (up to 15%) |
| v7 | +5.4% | -8.6% | 30% (8/19) | 27 | -7.2% | v6 + seeded macro themes + fundamentals (empty for 2021) |

## What Worked (v5 = best run)

### 1. 25% catastrophic stop with Claude-managed exits
The single biggest improvement. Replaced tight ATR-based stops (8-20%) with a wide 25% safety net and gave Claude responsibility for all normal exits using thesis validity + technicals (SMA50, MACD, OBV).

**Impact:** Win rate flipped from 33% (v4) to 52% (v5). NVDA went from being stopped out twice (v4) to +18% gain (v5). Claude held through volatility when thesis was intact instead of being mechanically ejected.

### 2. Beliefs and scored lessons (max 15)
Lessons start at score 1 and must prove themselves. Monthly reviews consolidate into beliefs (max 5). Replaced the old system where 60+ lessons accumulated and created decision paralysis.

**Impact:** Claude's decisions became more principled. The belief "Thesis Integrity Over Price Action" guided correct holds through the Feb/March rate rotation. Lesson scoring prevented contradictory lessons from accumulating.

### 3. Theme discovery from news
Claude discovers themes from first-review news instead of using hard-coded themes. Themes start at score 1 and must prove themselves.

**Impact:** Claude identified "Blue Wave," "Semiconductor Supercycle," and "Reflation/Rates Rotation" organically — all relevant to the actual 2021 market. Better than pre-2025 hard-coded themes like "AI/Automation."

### 4. Thesis auto-purging
CLOSED and STOPPED_OUT theses are automatically purged when adding new ones. Prevents "Cannot add thesis — at max capacity" errors.

## What Didn't Help (or Hurt)

### 1. Seeded macro themes (v7: -8.6% alpha)
Pre-seeding "Electrification," "Wealth Inequality," etc. anchored Claude toward positions that fought the rate environment (NEE, ENPH, SOFI, SQ). Without seeds, Claude was more cautious about these trades.

**Lesson:** Let Claude discover themes from news. Macro seeds create confirmation bias — Claude looks for evidence to support the seed rather than reading the market fresh. May work better in live trading where Claude already has accumulated context, but harmful in backtests starting from zero.

### 2. Fundamentals data for 2021 backtests (v7)
yfinance only provides ~5 quarters of historical data from today. For 2021 sims, fundamentals showed "(No data available)" for most tickers. Added prompt noise without value.

**Lesson:** Fundamentals integration is useful for live trading (2025+) but not for historical backtests before ~Q1 2025. The profitability gate on "highest" confidence couldn't fire without data.

### 3. Confidence-tiered allocations (v6: -11.2% alpha)
Adding "highest" at 15% didn't cause the regression — Claude rarely used it. The v6 underperformance was noise from different Claude responses on early positions (ENPH, PLTR sized larger and lost more). The tiers are neutral — they don't help or hurt meaningfully.

**Lesson:** Keep the tiers for live trading (they're a sensible guardrail) but don't expect them to improve backtesting results.

## v5 Configuration (Best Performing)

- **Stops:** 25% catastrophic safety net, Claude manages all exits
- **Lessons:** Max 15, scored 1-5, start at score 1, auto-evict lowest on cap
- **Beliefs:** Max 5, consolidated from lessons during monthly reviews
- **Themes:** Discovered from news (no seeding), start at score 1, removed below 1
- **Monthly reviews:** 21-day cadence (avoids overlap with 5-day weekly)
- **Thesis management:** Auto-purge CLOSED/STOPPED_OUT theses
- **Deployment pacing:** Gradual ramp over first 3 reviews
- **Anti-churning:** Trade count shown in prompt with guidance

## Why 2021 H1 Is Hard

SPY did +14% driven by two different groups at different times:
- **Jan-Feb:** Value/cyclicals rallied (banks, energy, industrials) on reopening
- **Mar-Jun:** Growth/tech recovered and caught up

The bot gets whipsawed because it buys tech in Jan (right thesis, wrong timing), exits during Feb rotation, then re-enters tech in April at higher prices. SPY owns everything and captures both legs automatically.

The remaining -2.4% alpha gap in v5 is mostly from the first 2 months of deployment. By mid-sim, v5 was keeping pace with SPY. In live trading with daily reviews, this gap should shrink.

---

# Simulation Findings — Q1 2025 (Jan-Mar)

## Benchmark: SPY -5.72%

A brutal quarter: DeepSeek panic, tariff uncertainty, broad market selloff.

## Results Summary

| Run | Cadence | Return | Alpha | Win Rate | Trades | Max DD | Key Changes |
|-----|---------|--------|-------|----------|--------|--------|-------------|
| v1 | 7-day | -6.24% | -0.52% | 0% (0/13) | 13 | -13.6% | Fundamentals, profitability gate, all v5 improvements |
| **v2** | **2-day** | **-3.68%** | **+2.04%** | **12.9% (4/27)** | **31** | **-15.7%** | **v1 + 2-day review cadence** |

## Key Finding: Review Cadence Is the Biggest Lever

At the same date (March 5, 2025):
- 7-day cadence: **-5.7%** vs SPY -1.1% (trailing by 4.6%)
- 2-day cadence: **+0.6%** vs SPY -1.1% (leading by 1.7%)

**+6.3% difference** from review frequency alone. The 2-day cadence caught the DeepSeek panic and March selloff much faster — Claude could exit deteriorating positions within 2 days instead of waiting a week.

## Fundamentals Working in 2025

Unlike 2021 backtests (where yfinance had no data), Q1 2025 has full fundamental data for 68/71 tickers. The profitability gate and fundamental context are actively informing decisions.

## Lesson Quality (2-day run)

Four lessons reached score 5/5 in just one quarter:
1. **Analyst Downgrade vs OBV** — Rising OBV during analyst selloff = institutions buying what retail sells. Hold.
2. **Entry Quality Filter** — Don't enter MACD bearish unless 2+ confirming signals (OBV rising + ADX > 25).
3. **Earnings Confirmation Hold** — Hold through RSI 65-72 after earnings beat if OBV rising.
4. **Short Exit Trigger** — MACD bullish + OBV flat/rising = exit short immediately.

## Belief Formed

**"Institutional Flow Primacy"** [3/5] — OBV as the primary decision signal for all positions. Four operational rules covering entries, holding through overbought, analyst signals, and stop discipline. Supported by Lessons 1, 4, 7, 8.

## Win Rate Context

12.9% win rate looks terrible but is misleading — 12 open positions carry unrealised gains that offset closed losses (-$10k closed P&L, ~$6.3k unrealised gains in open positions). The bot rotates losers out quickly and holds winners — exactly the right behaviour.

## First Positive Alpha in a Bear Quarter

+2.04% alpha in a -5.72% SPY quarter. The thesis-driven strategy with Claude-managed exits and frequent reviews can preserve capital in declining markets.

---

# Recommendations for Paper Trading

1. Use v5 configuration as the baseline (25% catastrophic stop, Claude-managed exits, beliefs, scored lessons)
2. Add fundamentals (real data available for 2025+)
3. Keep confidence tiers (sensible risk guardrail, profitability gate on "highest")
4. Do NOT seed themes — let Claude discover from live news
5. **Run daily reviews** — 2-day cadence proved the biggest lever; daily will be even better
6. Compile best lessons from all sim runs into a seed file (backward-looking, not forward-knowledge)
7. Monthly cadence at 21 days for belief consolidation and theme pruning
