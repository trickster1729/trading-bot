# ADR-004: Phase-Based Build (Shadow → Paper → Live)

**Date:** 2026-03-25
**Status:** Accepted

---

## Context

Building a live trading system carries real financial risk. Starting with live capital before the strategy and infrastructure are validated is how most amateur algorithmic trading projects lose money. We needed a structured approach to validate each layer before advancing.

Three failure modes to guard against:
1. **Strategy risk:** the signal logic doesn't actually have positive expectancy
2. **Infrastructure risk:** bugs in execution, risk management, or data handling cause unintended trades
3. **Capital risk:** deploying real money before the system is proven

## Decision

Build and validate in four sequential phases, with explicit gate criteria between each:

| Phase | Mode | Data | Orders | Money | Gate to advance |
|---|---|---|---|---|---|
| 1 — Shadow | `BOT_MODE=shadow` | Historical | Paper PnL only | None | Positive Sharpe on backtest |
| 2 — Paper Live | `BOT_MODE=paper` | Live feed | Paper account | None | 100+ trades, positive expectancy |
| 3 — Small Live | `BOT_MODE=live` | Live feed | Real orders | $5K–$10K | Profitable for 4+ weeks |
| 4 — Scale | `BOT_MODE=live` | Live feed | Real orders | Growing | Cloud deployed, monitored |

The same strategy code, risk manager, and execution interface runs across all phases. Only the `DataLoader` (historical vs. live) and `Broker` (paper vs. real) instances change. This is enforced by the ABC architecture (ADR-002).

## Rationale

- **Derisks capital deployment:** by the time real money is on the line, the strategy has been validated on historical data AND live paper trading
- **Catches infrastructure bugs early:** running in paper mode with live prices exposes data feed issues, broker API errors, and timing bugs before they cost money
- **Creates a paper trail:** each phase produces logs and DB records. Phase 2 results (100+ trades) become the empirical evidence for the research paper
- **Preserves optionality:** if Phase 2 results are poor, we iterate on the strategy without having lost real money

## Consequences

- **Slower time to live capital:** we won't deploy real money until Phase 3. Estimated 8–12 weeks from Phase 1 completion.
- **Paper ≠ live:** paper trading doesn't account for slippage, partial fills, or market impact. The PaperBroker's slippage model (configurable bps) mitigates this but doesn't eliminate it. Phase 2 results should be treated as an optimistic upper bound.
- **Mode enforcement:** `config.py` validates `BOT_MODE` at startup and raises if broker credentials are missing for paper/live mode. This prevents accidentally running live without credentials.
