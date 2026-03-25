# ADR-003: structlog for Structured Logging

**Date:** 2026-03-25
**Status:** Accepted

---

## Context

The trading bot must produce logs that serve two purposes simultaneously:
1. **Human-readable** during local development — easy to follow what the bot is doing in real time
2. **Machine-parseable** for post-hoc analysis — "why did the bot enter AAPL on Jan 15?" should be answerable by querying the log file

Standard library `logging` produces unstructured text. It can be configured to emit JSON, but the ergonomics are poor and adding context fields (symbol, strategy, confidence) to every log line requires manual string formatting.

## Decision

**structlog** with two output paths:

- **Console:** Rich-formatted, human-readable key=value output (for local dev and paper trading)
- **File:** newline-delimited JSON (for analysis, CloudWatch ingestion in Phase 3+)

Convention: no `print()` anywhere in the codebase. All output goes through `get_logger(__name__)`.

## Rationale

- **Structured by default:** `log.info("signal_generated", symbol="AAPL", confidence=0.82)` produces a queryable JSON event, not a string. Every context field is a first-class key.
- **Consistent format across all layers:** data, signals, risk, execution all emit the same format — log analysis tools don't need layer-specific parsers
- **Cloud-ready:** the JSON file format is compatible with AWS CloudWatch Logs Insights queries out of the box (Phase 3). No reformatting needed when we ship logs to the cloud.
- **Audit trail:** every trade decision is reconstructible from the log: signal generated → risk check → order submitted → fill. This is the source of truth for debugging and the raw data for the research paper.
- **Log levels used consistently:**
  - `DEBUG` — signal calculations, indicator values, internal loop state
  - `INFO` — trade decisions, order fills, risk approvals
  - `WARNING` — degraded conditions, risk blocks, slow data feeds
  - `ERROR` — broker rejections, data failures, kill switch events

## Consequences

- **Setup cost:** structlog requires one-time configuration at startup (`configure_logging()` in `cli.py`). Tests that import bot modules without calling this may see unformatted output — acceptable.
- **No print() discipline:** enforced by code review. `ruff` can be configured to flag `print()` calls (Phase 2 lint rule).
- **Log volume:** at DEBUG level, a 1-year daily backtest on 5 symbols generates ~50K log lines. Rotation is configured at 30 days.
