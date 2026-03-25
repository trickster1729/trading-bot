# ADR-002: Pluggable ABC Architecture for All Layers

**Date:** 2026-03-25
**Status:** Accepted

---

## Context

The trading bot needs to support multiple data sources, multiple strategies, multiple brokers, and multiple execution modes (shadow / paper / live) — and these need to be swappable without rewriting the core engine. The naive approach is to use `if/elif` branching inside the engine, but this creates tight coupling and makes testing and extension painful.

## Decision

Every major layer is defined by an **Abstract Base Class (ABC)** with a minimal, stable interface. Concrete implementations are injected at construction time (dependency injection). The engine never imports a concrete class directly.

| Layer | ABC | Current Implementations |
|---|---|---|
| Data | `DataLoader` | `YahooLoader` |
| Strategy | `Strategy` | `MomentumStrategy` |
| Broker | `Broker` | `PaperBroker` |
| (Risk) | *(not ABC — stateful)* | `RiskManager` |

## Rationale

- **Testability:** unit tests inject a `MagicMock` or a simple fake (e.g. `AlwaysLongStrategy`) without touching real networks or real money
- **Extensibility without modification:** adding `AlpacaBroker` means creating one new file; nothing in `BacktestEngine` changes
- **Parity across modes:** the exact same strategy code runs in backtest, paper, and live — only the `DataLoader` and `Broker` instances differ. This is the single most important property for trusting live results
- **Phase 4 readiness:** NautilusTrader (our target architecture) uses the same adapter pattern; building toward it now reduces Phase 4 migration cost

## Consequences

- **Boilerplate cost:** every new data source or broker requires subclassing, not just a function. Accepted — the structure pays dividends at scale.
- **Interface stability:** the ABCs must be designed carefully. Changing `Strategy.generate_signals()` signature after Phase 2 would require updating all implementations. We mitigate this with conservative interface design (see the Signal dataclass `metadata` dict for extensibility without schema changes).
- **No global state:** each component receives its dependencies; nothing reads from global singletons. This is enforced by design, not convention.
