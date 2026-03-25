# ADR-009: Mitigating the Paper-to-Live Gap

**Date:** 2026-03-25
**Status:** Accepted

---

## Context

Paper trading results are an optimistic upper bound on live results. The gap has several components that, if unaddressed, can cause a strategy that looks profitable on paper to lose money in live trading. Before deploying real capital in Phase 3, we need to understand and partially close this gap.

**The three main sources of paper-to-live divergence:**

| Source | Paper assumption | Live reality | Severity |
|---|---|---|---|
| **Slippage** | Fixed bps model at signal price | Actual fill depends on order book depth, time-of-day, order size | Medium |
| **Bid-ask spread** | Not explicitly modelled | Buy at ask, sell at bid — round-trip cost of 1–50 bps depending on liquidity | High for illiquid names |
| **Partial fills** | Always 100% filled | Limit orders may fill partially; market orders in thin books may move price | Low for liquid names |
| **Latency** | Instantaneous execution | Real orders take 50–500ms; price may move between signal and fill | Low for daily bars |
| **Market impact** | None | Large orders relative to daily volume move price against us | Low at our capital size |

## Decision

Apply four specific mitigations, in increasing order of implementation cost:

### Mitigation 1: Conservative slippage defaults (Phase 1 — implemented now)
- Default `PaperBroker.slippage_bps` = **10 bps** (not 5)
- For crypto: **20 bps** (wider spreads, more volatile fills)
- For illiquid names: reject if ADV filter fails (see Mitigation 3)
- This pessimistic assumption means paper results are a conservative lower bound, not an optimistic upper bound

### Mitigation 2: Bid-ask spread simulation (Phase 2)
- Add `spread_bps` parameter to `PaperBroker`
- Buy fills at `price × (1 + (slippage_bps + spread_bps/2) / 10_000)`
- Sell fills at `price × (1 - (slippage_bps + spread_bps/2) / 10_000)`
- Default spread estimates by asset class:
  - S&P 500 constituents: 2–5 bps
  - Crypto top-10: 5–10 bps
  - Everything else: 15–30 bps (avoid these names entirely — see Mitigation 3)

### Mitigation 3: Restrict to liquid names via symbol selection (Phase 1–2)
- At $10K capital with 2% sizing, each order is **$200**. AAPL's daily dollar volume is ~$9B — our order is 0.000002% of it. Market impact is zero at this scale. A runtime ADV filter would never trigger and is not worth building.
- Market impact only becomes relevant at $5M+ capital. That is a Phase 4 concern.
- The practical mitigation: trade only liquid, well-known names — S&P 500 stocks and BTC/ETH/SOL for crypto. Avoid micro-caps and low-liquidity pairs in Phase 1–3.
- Implementation: a recommended symbol list in `bot/config.py`, not a runtime check.

### Mitigation 4: Live slippage calibration (Phase 3)
- After Phase 2 paper live run, compare paper fill prices vs. Alpaca's actual simulated fill prices
- Compute actual vs. modelled slippage per symbol and time-of-day
- Update `slippage_bps` defaults based on observed data
- This makes the model data-driven rather than estimated

## The residual risk

Even with all mitigations applied, two gaps remain that cannot be fully closed with paper trading:

1. **Regime change:** a strategy that worked in 2022–2024 backtests may not work in 2025 live conditions if market regime changes. Mitigation: use out-of-sample test periods in Phase 1 backtests (never test on the same period used to tune parameters).

2. **Execution quality:** Alpaca's paper fills are simulated — they don't reflect the actual order book state at execution time. Phase 3 live results on small capital ($200 orders) are the only true calibration. This is why Phase 3 starts small.

## Consequences

- **PaperBroker default changed:** `slippage_bps` default raised to 10 bps (from 5). Existing backtest results will show slightly lower returns. This is intentional — we want conservative estimates.
- **Phase 2 add:** `spread_bps` parameter to `PaperBroker` and ADV filter to `RiskManager`
- **Capital risk mitigation:** the combination of (a) small starting capital in Phase 3, (b) 2% position sizing, (c) 10% kill switch, and (d) conservative paper assumptions means the maximum realistic Phase 3 loss before halting is approximately **$1,000 on a $10,000 account** (10% drawdown × $10,000). This is a knowable, bounded loss, not an open-ended risk.
- **Updated plan ADR index:** see ADR-009 in `docs/adr/README.md`
