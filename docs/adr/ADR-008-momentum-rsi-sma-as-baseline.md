# ADR-008: RSI + SMA Momentum as Phase 1 Baseline Strategy

**Date:** 2026-03-25
**Status:** Accepted

---

## Context

Phase 1 needs at least one working strategy to validate the end-to-end pipeline. The strategy must be:
- Simple enough to reason about (no black-box ML)
- Fast to compute (no GPU, works on a laptop)
- Well-documented in literature (so backtest results can be contextualised)
- A valid baseline to beat with more sophisticated strategies later

The strategy does not need to be the best possible signal — it needs to prove the pipeline works and produce some interpretable results.

Strategies considered for Phase 1 baseline:
- RSI only: simple but generates too many false signals in trending markets
- Moving average crossover (SMA/EMA): standard, but generates many whipsaws in sideways markets
- **RSI + SMA confirmation:** RSI identifies overextension, SMA confirms trend direction — combines two independent signals
- Bollinger Bands: mean-reversion signal, but adds complexity with no clear advantage over RSI for a baseline
- MACD: momentum + divergence, reasonable baseline but harder to tune

## Decision

**RSI (14-period, default) + SMA (20-period, default) momentum strategy.**

Signal logic:
- **LONG:** RSI < 30 (oversold) AND price < SMA (trend confirmation)
- **SHORT:** RSI > 70 (overbought) AND price > SMA (trend confirmation)
- **Confidence:** derived from RSI extremity — further from threshold = higher confidence

All parameters are configurable from the CLI:
```
bot backtest --rsi-period 7 --sma-period 50 --oversold 25 --overbought 75
```

## Rationale

- **Requiring both conditions filters noise:** RSI alone fires too often in trending markets. The SMA filter requires that the trend direction is consistent with the RSI signal — reducing false positives.
- **Interpretable:** every signal can be explained as "RSI hit 22 while price was below the 20-day average — oversold bounce signal with confidence 0.71". This is essential for the research paper and for debugging.
- **Parameter sweep friendly:** all parameters are injected at construction time and stored in `Signal.metadata` and `BacktestRun.params`. Running systematic parameter sweeps is straightforward.
- **Well-studied:** RSI and moving average crossover strategies have decades of academic literature. Our results can be compared to known benchmarks.
- **Beats the obvious alternative:** a buy-and-hold benchmark on AAPL or SPY is the natural comparison. The strategy should beat this before we declare Phase 1 complete.

## Consequences

- **Mean-reversion bias:** the RSI oversold/overbought interpretation is a mean-reversion signal, not a trend-following signal. This strategy may underperform in strong directional trends. Phase 2 will add a dedicated trend-following strategy (e.g. SMA crossover) to cover this regime.
- **No position exit signal:** Phase 1 exits are stop-loss only (2% adverse move). There is no explicit take-profit or trailing stop from the strategy itself. This limits upside capture. Phase 2 enhancement.
- **Not suitable for all assets as-is:** RSI thresholds optimal for equities (30/70) may not be appropriate for crypto (which can stay oversold for extended periods). The `asset_class` parameter on `MomentumStrategy` allows per-class threshold configuration.

## Strategy evolution path

| Phase | Strategy additions |
|---|---|
| 1 (now) | RSI + SMA momentum (this ADR) |
| 2 | Mean reversion (Bollinger Bands), SMA crossover (trend-following) |
| 3 | LLM sentiment scoring (Claude API), ML classifier (XGBoost on OHLCV features) |
| 4 | RL agent (PPO/SAC) for execution timing and position sizing |
