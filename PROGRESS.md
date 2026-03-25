# Trading Bot — Progress Tracker

Last updated: 2026-03-25 (session 4 — health + alerts, git init, CLAUDE.md)

---

## Current Phase: Phase 1 — CLI + Shadow Trading

---

## Phase 1 Scaffold Checklist

### Infrastructure
- [x] `pyproject.toml` — dependencies (typer, yfinance, ccxt, structlog, rich, pandas, numpy, httpx)
- [x] `.env.example` — placeholder credentials template
- [x] `.gitignore`

### Monitoring
- [x] `bot/monitoring/logger.py` — structlog setup, JSON file + Rich console output
- [x] `bot/monitoring/metrics.py` — in-memory PerformanceTracker (PnL, Sharpe, win rate, drawdown)
- [x] `bot/monitoring/health.py` — heartbeat loop (data feed + broker + strategy checks)
- [x] `bot/monitoring/alerts.py` — alert dispatcher (ConsoleChannel + LogChannel, Phase 1)

### Data Layer
- [x] `bot/data/base.py` — DataLoader ABC + Bar dataclass
- [x] `bot/data/yahoo.py` — YahooLoader (yfinance, free historical)
- [x] `bot/data/ccxt_loader.py` — crypto via CCXT (Binance default, pagination, interval mapping)
- [ ] `bot/data/alpaca.py` — Alpaca live data (Phase 2)

### Signal / Strategy Layer
- [x] `bot/signals/base.py` — Strategy ABC, Signal dataclass, Direction/AssetClass/SignalStrength enums
- [x] `bot/signals/momentum.py` — RSI + SMA momentum strategy with confidence scoring
- [ ] `bot/signals/mean_reversion.py` — (Phase 1+)

### Risk Layer
- [x] `bot/risk/manager.py` — RiskManager with kill switch, fixed fractional sizing, all gates logged
- [x] `bot/risk/limits.py` — RiskLimits with per-asset-class overrides

### Execution Layer
- [x] `bot/execution/base.py` — Broker ABC, OrderResult dataclass, OrderStatus/OrderSide enums
- [x] `bot/execution/paper.py` — PaperBroker with slippage model, commission, position tracking
- [ ] `bot/execution/alpaca.py` — Alpaca live (Phase 2+)
- [ ] `bot/execution/binance.py` — Binance live (Phase 2+)

### Backtest
- [x] `bot/backtest/engine.py` — historical replay loop, rolling window, stop-loss exits
- [x] `bot/backtest/report.py` — Rich-formatted terminal report (PnL, Sharpe, win rate, trade list)

### CLI & Config
- [x] `bot/config.py` — Settings dataclass, all env vars centralised, validate() method
- [x] `bot/cli.py` — Typer CLI (`bot backtest`, `bot report`) with full param overrides

### Database
- [x] `bot/db/models.py` — SQLAlchemy models: BacktestRun, TradeRecord, SignalRecord
- [x] `bot/db/store.py` — TradeStore with full CRUD + analytics queries (SQLite now → Postgres Phase 3)

### Tests
- [x] `tests/conftest.py` — shared fixtures, synthetic bar factories, signal factory
- [x] `tests/test_signals.py` — Signal dataclass, RSI/SMA math, MomentumStrategy logic (35+ cases)
- [x] `tests/test_risk.py` — all RiskManager gates, kill switch, position sizing, per-asset limits (25+ cases)
- [x] `tests/test_execution.py` — PaperBroker fills, slippage, commissions, cancellations (25+ cases)
- [x] `tests/test_backtest.py` — BacktestEngine flow, PerformanceTracker math (25+ cases)
- [x] `tests/test_monitoring.py` — health checkers, monitor loop, alert dispatcher, channels (37 cases)

### Build & Execution
- [x] `Makefile` — `make install/test/backtest/lint/clean` + db query shortcuts
- [x] `pyproject.toml` — coverage exclusion rules (80% gate on real logic only)

### Docs
- [x] `README.md` — project overview, quickstart, architecture diagram, phase roadmap
- [x] `docs/RUNBOOK.md` — setup, backtest commands, test commands, log queries, phase gates
- [x] `docs/plan/v1-2026-03-25.md` — first draft plan
- [x] `docs/plan/v2-2026-03-25.md` — updated after session 3 (DB, Makefile, ADRs, test suite)
- [x] `docs/adr/README.md` — ADR index
- [x] `docs/adr/ADR-001` — Python as primary language
- [x] `docs/adr/ADR-002` — Pluggable ABC architecture
- [x] `docs/adr/ADR-003` — structlog for structured logging
- [x] `docs/adr/ADR-004` — Phase-based build (shadow → paper → live)
- [x] `docs/adr/ADR-005` — Fixed fractional position sizing
- [x] `docs/adr/ADR-006` — SQLAlchemy + SQLite → PostgreSQL migration path
- [x] `docs/adr/ADR-007` — Alpaca + Binance as primary brokers
- [x] `docs/adr/ADR-008` — RSI + SMA momentum as Phase 1 baseline strategy
- [x] `CLAUDE.md` — project rules, architecture constraints, testing standards, context management
- [x] `git` — repository initialized, commits in progress

### Scripts
- [ ] `scripts/seed_historical.py` — download and cache historical data

---

## Phase 1 Gate Criteria

> Must show at least one profitable signal with positive expectancy before moving to Phase 2.

- [ ] End-to-end backtest runs without errors
- [ ] Momentum strategy produces signals on real historical data
- [ ] `bot report` prints Sharpe, win rate, drawdown, PnL curve
- [ ] All trade decisions logged in structured JSON

---

## Upcoming Phases (not started)

### Phase 2 — Paper Live Trading
- Connect to live market feeds (Alpaca WebSocket, Binance testnet)
- Run 4–8 weeks, log every trade decision
- Gate: positive expectancy over 100+ trades

### Phase 3 — Small Live Trading
- Real capital ($5K–$10K), strict 2% per trade, 10% drawdown kill switch
- Logs → AWS CloudWatch; alerts → AWS SNS

### Phase 4 — Scale + Cloud
- Docker + AWS ECS Fargate
- Prometheus + Grafana dashboard
- Multi-strategy portfolio

---

## Key Decisions & Notes

| Topic | Decision |
|---|---|
| Language | Python 3.11+ |
| CLI | Typer |
| Logging | structlog (JSON file + Rich console) |
| Stock data (Phase 1) | yfinance (free, no key) |
| Crypto data | CCXT |
| Paper broker (Phase 1–2) | Alpaca paper + PaperBroker class |
| Live broker (stocks) | Alpaca → IBKR |
| Live broker (crypto) | Binance → Coinbase Adv. |
| Risk: max per trade | 2% of account |
| Risk: drawdown halt | 10% — hard kill switch |
| No leverage | Phase 1–2 |
| No overnight positions | Phase 1 (equities) |
| Target return | 5–10%+ monthly |
| Starting capital | $5K–$10K (once live) |

---

## Reference Codebases

| What we're building | Primary reference |
|---|---|
| `bot/backtest/engine.py` | `backtesting.py` strategy/backtest pattern |
| `bot/execution/paper.py` | `AutoTrader` virtual broker |
| `bot/execution/alpaca.py` | `FinRL-Trading` alpaca_manager.py |
| `bot/signals/momentum.py` | `backtesting.py` + `Jesse` strategy examples |
| `bot/signals/sentiment.py` | `claude-investor` Claude API prompting pattern (Phase 3) |
| `bot/signals/rl_agent.py` | `FinRL` DRL pipeline (Phase 4) |
