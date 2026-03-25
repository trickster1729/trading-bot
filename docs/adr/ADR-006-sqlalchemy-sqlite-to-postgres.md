# ADR-006: SQLAlchemy with SQLite → PostgreSQL Migration Path

**Date:** 2026-03-25
**Status:** Accepted

---

## Context

The trading bot needs persistent storage for:
- Trade records (every closed position with full signal context)
- Signal records (every signal generated, approved and blocked)
- Backtest run metadata (parameters, performance metrics)

In Phase 1, the system runs locally on a single machine and trade volume is low (tens of trades per backtest run). In Phase 3–4, the system runs in the cloud, potentially generating thousands of signals per day across multiple strategies.

Options considered:
- **Raw SQLite with `sqlite3`:** minimal dependencies, zero config, but no ORM — all queries as raw SQL strings, fragile to schema changes
- **SQLAlchemy + SQLite:** ORM + clean Python models, SQLite for storage
- **SQLAlchemy + PostgreSQL directly:** production-grade but requires running a Postgres server locally in Phase 1
- **MongoDB / document store:** flexible schema, but overkill for structured trade data
- **DuckDB:** excellent for analytical queries, but less mature for transactional workloads

## Decision

**SQLAlchemy ORM with SQLite in Phase 1–2, switchable to PostgreSQL in Phase 3** by changing one environment variable:

```env
# Phase 1–2 (local)
DB_URL=sqlite:///trading.db

# Phase 3+ (cloud)
DB_URL=postgresql+psycopg2://user:pass@host:5432/tradingbot
```

No application code changes required for this switch. SQLAlchemy handles the dialect translation.

## Rationale

- **Zero local infrastructure:** SQLite requires no running server. `make install` is all that's needed.
- **ORM benefits now:** typed Python models (`TradeRecord`, `SignalRecord`, `BacktestRun`) are readable, refactorable, and IDE-navigable. Raw SQL strings are not.
- **Migration path is explicit and cheap:** one URL change. The `TradeStore.from_url()` constructor makes this a one-line config change.
- **JSON columns work on both:** SQLAlchemy's `JSON` type maps to SQLite's JSON text and PostgreSQL's native `JSONB` — signal metadata and strategy params are stored the same way on both backends.
- **Test isolation:** `TradeStore.for_testing()` uses in-memory SQLite (`sqlite:///:memory:`) — each test gets a fresh, isolated database with no files left on disk.
- **Phase 4 enhancement:** TimescaleDB (PostgreSQL extension for time-series) can be dropped in without changing any application code, just the connection URL.

## Consequences

- **SQLite limitations:** SQLite doesn't support concurrent writes well. In Phase 1–2 this is fine (single process). In Phase 3 (cloud, potentially multiple workers), we must switch to PostgreSQL before deploying.
- **WAL mode enabled:** for Phase 2 (paper live, single process with background logging), SQLite is configured with WAL (Write-Ahead Logging) mode which allows concurrent reads during writes.
- **Schema migrations:** SQLAlchemy's `create_all()` handles initial schema creation but not migrations after the schema changes. When we alter models, we'll use **Alembic** (SQLAlchemy's migration tool). This is a Phase 2 addition — for Phase 1, dropping and recreating the DB is acceptable.
- **`metadata` column naming:** SQLAlchemy reserves the `metadata` attribute name on all ORM models (it holds the `MetaData` instance). Signal and strategy context fields must use `signal_metadata` or similar names — a minor naming constraint discovered during implementation.
