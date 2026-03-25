# CLAUDE.md — Trading Bot

This file guides Claude Code in every session. Keep it concise — only rules Claude can't infer from code.

---

## Project overview

Algorithmic trading bot (Phase 1: CLI + shadow trading). See `PROGRESS.md` for current status,
`docs/plan/v2-2026-03-25.md` for architecture, `docs/adr/` for design decisions.

---

## How to run things

```bash
make install        # install all deps (uv or pip)
make test           # run full test suite with coverage
make backtest       # run a backtest on AAPL 2022–2024
make lint           # ruff check
```

Tests use pytest. Coverage gate is 80% on real logic (see pyproject.toml exclusions).

---

## Architecture rules (non-negotiable)

- **Pluggable ABCs everywhere**: DataLoader, Strategy, Broker, RiskManager each have an ABC.
  Never hardcode a concrete implementation in the core loop.
- **Layer separation**: data → signals → risk → execution → persistence. No layer reaches back up.
- **Classes ≤300 lines, files ≤400 lines**. Split when approaching the limit. Prefer multiple
  focused classes over one large one.
- **No hardcoded symbols, thresholds, or credentials**. All config lives in `bot/config.py`
  (Settings dataclass) read from environment variables.

## Code style

- Python 3.11+. Type hints everywhere. `from __future__ import annotations`.
- `log = get_logger(__name__)` at module top. Never use `print()`.
- structlog calls use keyword args: `log.info("event_name", key=value)`.
- Round monetary values to 6 decimals, commissions to 4.

## Testing rules

- 80%+ coverage on business logic. Trivial wrappers and CLI entry points are excluded.
- Tests must catch real bugs — not just happy paths. Include edge cases, boundary conditions,
  and error paths.
- Use `TradeStore.for_testing()` for DB tests (in-memory SQLite, no teardown files).
- Synthetic bar factories are in `tests/conftest.py` — use them, don't duplicate.

## Risk rules (never relax without a new ADR)

- Max 2% of account per trade (`RiskLimits.max_position_pct`).
- 10% drawdown → kill switch (hard halt, no exceptions).
- No leverage in Phase 1–2. No overnight positions for equities in Phase 1.
- Default slippage: 10 bps equities, 20 bps crypto (see ADR-009).

## Context management

- Use `/clear` between unrelated tasks to keep context clean.
- For broad exploration (finding a pattern across many files), use the Explore subagent.
- After completing a chunk of work: commit, then `/clear` before the next unrelated feature.
- Keep PROGRESS.md and docs/plan/ up to date — these are the source of truth for session state.

## When to write an ADR

Any choice where a future contributor would ask "why?":
- Choosing between two reasonable alternatives
- A decision that's expensive to reverse
- A runtime tradeoff (e.g. latency vs. accuracy)

Add to `docs/adr/` and update `docs/adr/README.md`.

## Phase 1 gate (must pass before Phase 2)

- [ ] End-to-end backtest runs without errors on real yfinance data
- [ ] Momentum strategy produces signals
- [ ] `bot report` prints Sharpe, win rate, drawdown, PnL
- [ ] All trade decisions logged in structured JSON
