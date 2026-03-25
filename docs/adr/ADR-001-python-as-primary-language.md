# ADR-001: Python as Primary Language

**Date:** 2026-03-25
**Status:** Accepted

---

## Context

We needed to choose a primary language for the trading bot. The system spans data ingestion, signal generation, risk management, order execution, and eventually ML/RL model training. The language choice affects available libraries, execution speed, deployment simplicity, and the ability to hire or collaborate.

Candidates considered:
- **Python** — dominant in quant finance and ML
- **Go** — excellent concurrency, fast, but thin quant ecosystem
- **Rust** — maximum performance, used in NautilusTrader (our Phase 4 reference), but steep learning curve
- **Julia** — strong in numerical computing, but small community

## Decision

**Python 3.11+**

## Rationale

- **Ecosystem depth:** pandas, numpy, yfinance, CCXT, Alpaca SDK, scikit-learn, PyTorch, FinRL, VectorBT — every library we need exists and is mature
- **Phase 1 velocity:** we can prototype a working backtest in hours, not days
- **ML/RL path:** Phase 3–4 require ML signal generation and RL agents; Python is the only practical choice for this
- **Operator familiarity:** both authors know Python well; no ramp-up cost
- **Speed is not the bottleneck in Phase 1–3:** daily bar backtests and paper trading do not require microsecond latency; the bottleneck is strategy logic, not language speed
- **Phase 4 escape hatch:** NautilusTrader (our Phase 4 reference) uses Rust for the hot path but exposes a Python strategy API — we can migrate performance-critical execution to Rust without rewriting strategy logic

## Consequences

- **Accepted trade-off:** Python is slower than Go/Rust for the execution hot path. In Phase 4 (live, high-frequency), we may need to push order routing to a compiled layer. The pluggable Broker interface isolates this risk — swapping the execution layer does not affect strategies or risk logic.
- **GIL limitation:** true parallelism for CPU-bound signal computation requires multiprocessing, not threading. Phase 3+ ensemble strategies will use `ProcessPoolExecutor`.
- **Minimum version:** 3.11+ for `match` statements, `tomllib`, and performance improvements. `3.13` is used in the development environment.
