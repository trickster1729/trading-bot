# Trading Bot

An algorithmic trading system for Stocks, Crypto, and Options. Built in phases — from a local backtesting CLI to a cloud-hosted, fully automated multi-asset engine.

**Status:** Phase 1 — CLI + Shadow Trading (backtesting, no real money)

---

## Goals

- Target: 5–10%+ monthly returns with controlled drawdown
- Starting capital (once live): $5,000–$10,000
- Risk first: max 2% per trade, 10% drawdown halts all trading
- Long-term: cloud-hosted, multi-strategy, multi-asset

---

## Quickstart

```bash
# 1. Install
python3.11 -m venv .venv && source .venv/bin/activate
make install

# 2. Configure
cp .env.example .env   # edit if needed — defaults work for Phase 1

# 3. Run a backtest
make backtest

# 4. Custom symbols / dates
make backtest SYMBOLS=AAPL,NVDA,MSFT START=2022-01-01 END=2024-01-01

# 5. Run tests
make test
```

See [docs/RUNBOOK.md](docs/RUNBOOK.md) for the full operational guide.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     CLI  (bot backtest)                 │
└───────────────────────────┬─────────────────────────────┘
                            │
          ┌─────────────────▼─────────────────┐
          │         BacktestEngine             │
          │  (bar-by-bar historical replay)    │
          └──┬──────────┬──────────┬───────────┘
             │          │          │
    ┌────────▼───┐ ┌────▼────┐ ┌──▼──────────┐
    │  DataLoader│ │ Strategy│ │ RiskManager │
    │  (Yahoo /  │ │ (RSI+SMA│ │ (2% pos size│
    │  Alpaca /  │ │  et al.)│ │  10% halt)  │
    │   CCXT)    │ └────┬────┘ └──────┬──────┘
    └────────────┘      │             │
                        └──────┬──────┘
                               │ Signal + RiskResult
                        ┌──────▼──────┐
                        │   Broker    │
                        │  (Paper /   │
                        │  Alpaca /   │
                        │  Binance)   │
                        └──────┬──────┘
                               │ OrderResult
                  ┌────────────▼────────────┐
                  │  PerformanceTracker +    │
                  │      TradeStore          │
                  │  (SQLite → Postgres)     │
                  └─────────────────────────┘
```

Every layer is pluggable. Adding a new strategy, broker, or data source means implementing one interface — no changes to the engine or risk layer.

---

## Adding a Strategy

1. Create `bot/signals/your_strategy.py`
2. Subclass `Strategy`, implement `generate_signals(bars, symbol) -> list[Signal]`
3. Pass it to `BacktestEngine` — everything else (risk, execution, logging, DB) is handled

```python
from bot.signals.base import Strategy, Signal, Direction, AssetClass
import pandas as pd

class MyStrategy(Strategy):
    name = "my_strategy"
    asset_class = AssetClass.EQUITY

    def warm_up_bars(self) -> int:
        return 20

    def generate_signals(self, bars: pd.DataFrame, symbol: str) -> list[Signal]:
        # your logic here
        return [Signal(symbol=symbol, direction=Direction.LONG, confidence=0.75, ...)]
```

---

## Adding a Broker

1. Create `bot/execution/your_broker.py`
2. Subclass `Broker`, implement `submit_order()` and `cancel_order()`
3. Pass it to `BacktestEngine` in `bot/cli.py`

---

## Phase Roadmap

| Phase | Status | Description |
|---|---|---|
| 1 — CLI + Shadow | **In progress** | Historical backtest, paper PnL, no real money |
| 2 — Paper Live | Planned | Live prices, Alpaca paper + Binance testnet |
| 3 — Small Live | Planned | Real capital ($5K–$10K), kill switch, cloud logs |
| 4 — Scale + Cloud | Planned | Docker + AWS ECS, Grafana, multi-strategy portfolio |

---

## Project Structure

```
trading-bot/
├── Makefile                 # all dev commands
├── PROGRESS.md              # living build checklist
├── pyproject.toml           # package + tool config
├── .env.example             # env var template
│
├── bot/
│   ├── cli.py               # Typer entrypoint: bot backtest, bot report
│   ├── config.py            # all env vars in one place
│   ├── data/                # DataLoader ABC + Yahoo / Alpaca / CCXT implementations
│   ├── signals/             # Strategy ABC + Signal dataclass + implementations
│   ├── risk/                # RiskManager, RiskLimits, position sizing
│   ├── execution/           # Broker ABC + PaperBroker / Alpaca / Binance
│   ├── backtest/            # BacktestEngine + report rendering
│   ├── monitoring/          # structlog setup, PerformanceTracker, health, alerts
│   └── db/                  # SQLAlchemy models + TradeStore
│
├── tests/                   # pytest suite (94% coverage)
├── docs/
│   ├── RUNBOOK.md           # operational guide
│   ├── adr/                 # Architecture Decision Records
│   └── plan/                # versioned plan snapshots
└── scripts/                 # seed_historical.py etc.
```

---

## Risk Rules (non-negotiable)

- Max **2% of account** per trade
- Max **10% drawdown** → halt all trading, alert
- No leverage in Phase 1–2
- No overnight positions in Phase 1 (equities)
- Every trade logged with full context: signal, confidence, entry, exit, PnL
- Kill switch: halts all open orders immediately

---

## Key Commands

```bash
make install                         # install deps
make test                            # run tests (80% coverage gate)
make backtest                        # AAPL + MSFT, 2023, $10k
make backtest SYMBOLS=BTC-USD        # crypto backtest
make backtest-debug                  # with DEBUG logging
make smoke                           # quick 1-month smoke test
make db-trades                       # show last 20 trades from DB
make db-runs                         # show last 10 backtest runs
make logs-trades                     # tail filled orders in log
```

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | Best quant ecosystem |
| CLI | Typer | Clean, typed, easy to extend |
| Data (stocks) | yfinance + Alpaca | Free historical + live feed |
| Data (crypto) | CCXT | Unified API across 100+ exchanges |
| Broker (stocks) | Alpaca → IBKR | Simple REST, free paper trading |
| Broker (crypto) | Binance → Coinbase Adv. | Best liquidity, testnet available |
| Database | SQLite → PostgreSQL | Zero-config now, scalable later |
| Logging | structlog | Structured JSON + Rich console |
| Cloud (Phase 4) | AWS ECS Fargate | Familiar, cost-effective |

---

## Documentation

- [RUNBOOK.md](docs/RUNBOOK.md) — setup, commands, debugging, log queries
- [Architecture Decisions](docs/adr/) — why key decisions were made
- [Plan snapshots](docs/plan/) — versioned plan evolution
- [PROGRESS.md](PROGRESS.md) — current build status
