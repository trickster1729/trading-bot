# Architecture Decision Records

This directory records significant architectural decisions made during the development of the trading bot. Each ADR captures the context, the decision, the reasoning, and the consequences — including trade-offs accepted.

## Why ADRs

Decisions that seem obvious today are mysterious six months later. ADRs answer "why does it work this way?" without requiring someone to read the git history or ask the original author. They also make the research paper stronger: reviewers want to see that design choices were deliberate.

## Status vocabulary

| Status | Meaning |
|---|---|
| **Accepted** | Active decision, currently in force |
| **Superseded** | Replaced by a later ADR (link provided) |
| **Deprecated** | No longer relevant, kept for history |
| **Proposed** | Under discussion, not yet finalised |

## Index

| # | Title | Status | Date |
|---|---|---|---|
| [ADR-001](ADR-001-python-as-primary-language.md) | Python as primary language | Accepted | 2026-03-25 |
| [ADR-002](ADR-002-pluggable-abc-architecture.md) | Pluggable ABC architecture for all layers | Accepted | 2026-03-25 |
| [ADR-003](ADR-003-structlog-for-logging.md) | structlog for structured logging | Accepted | 2026-03-25 |
| [ADR-004](ADR-004-phase-based-build.md) | Phase-based build: shadow → paper → live | Accepted | 2026-03-25 |
| [ADR-005](ADR-005-fixed-fractional-position-sizing.md) | Fixed fractional position sizing | Accepted | 2026-03-25 |
| [ADR-006](ADR-006-sqlalchemy-sqlite-to-postgres.md) | SQLAlchemy with SQLite → PostgreSQL migration path | Accepted | 2026-03-25 |
| [ADR-007](ADR-007-alpaca-binance-as-primary-brokers.md) | Alpaca + Binance as Phase 1–3 brokers | Accepted | 2026-03-25 |
| [ADR-008](ADR-008-momentum-rsi-sma-as-baseline.md) | RSI + SMA momentum as Phase 1 baseline strategy | Accepted | 2026-03-25 |
| [ADR-009](ADR-009-paper-to-live-gap-mitigations.md) | Mitigating the paper-to-live gap | Accepted | 2026-03-25 |
