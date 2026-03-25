# Trading Bot — Runbook

Operational guide for running, testing, and debugging the bot at each phase.
Updated as new capabilities are added.

---

## Table of Contents

1. [Environment Setup](#1-environment-setup)
2. [Running a Backtest](#2-running-a-backtest)
3. [Running the Test Suite](#3-running-the-test-suite)
4. [Interpreting Backtest Results](#4-interpreting-backtest-results)
5. [Tuning Strategy Parameters](#5-tuning-strategy-parameters)
6. [Debugging Common Issues](#6-debugging-common-issues)
7. [Logs Reference](#7-logs-reference)
8. [Phase Readiness Checklist](#8-phase-readiness-checklist)

---

## 1. Environment Setup

### First-time setup

```bash
# Clone and enter the project
cd trading-bot

# Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install the bot and all dependencies
pip install -e ".[dev]"

# Copy env template and fill in values
cp .env.example .env
```

### `.env` values for Phase 1 (shadow/backtest)

```env
BOT_MODE=shadow
INITIAL_CAPITAL=10000
LOG_LEVEL=INFO
LOG_FILE=logs/trading.log
```

No API keys needed for Phase 1. `BOT_MODE=shadow` means no broker connections.

### Verify installation

```bash
bot --help
# Should print: Usage: bot [OPTIONS] COMMAND [ARGS]...
```

---

## 2. Running a Backtest

### Basic backtest (equity, 1 year)

```bash
bot backtest --symbols AAPL,MSFT --start 2023-01-01 --end 2024-01-01
```

### Crypto backtest

```bash
bot backtest --symbols BTC-USD,ETH-USD --start 2022-01-01 --end 2023-01-01
```

### Custom starting capital

```bash
bot backtest --symbols AAPL --start 2023-01-01 --end 2024-01-01 --capital 5000
```

### Tune strategy parameters inline

```bash
bot backtest \
  --symbols AAPL,NVDA,MSFT \
  --start 2022-01-01 --end 2024-01-01 \
  --rsi-period 7 \
  --sma-period 50 \
  --oversold 25 \
  --overbought 75 \
  --capital 10000
```

### Intraday backtest (hourly bars)

```bash
bot backtest --symbols AAPL --start 2023-06-01 --end 2023-09-01 --interval 1h
```

### Debug mode (see every signal calculation)

```bash
bot backtest --symbols AAPL --start 2023-01-01 --end 2024-01-01 --log-level DEBUG
```

---

## 3. Running the Test Suite

### Run all tests

```bash
pytest
```

### Run with coverage report

```bash
pytest --cov=bot --cov-report=term-missing
```

### Run a specific test file

```bash
pytest tests/test_risk.py -v
```

### Run a specific test class or test

```bash
pytest tests/test_risk.py::TestKillSwitch -v
pytest tests/test_risk.py::TestKillSwitch::test_drawdown_breach_trips_kill_switch -v
```

### Run tests and stop on first failure

```bash
pytest -x
```

### Run tests matching a keyword

```bash
pytest -k "kill_switch" -v
pytest -k "confidence" -v
```

### Coverage target

```bash
pytest --cov=bot --cov-report=term-missing --cov-fail-under=80
```

---

## 4. Interpreting Backtest Results

The report prints three panels:

### Activity panel

| Field | What it means |
|---|---|
| Bars processed | Number of OHLCV candles the engine stepped through |
| Signals generated | Number of signal objects returned by the strategy |
| Orders submitted | Signals that passed the risk manager |
| Orders filled | Orders actually executed by the paper broker |
| Closed trades | Positions that were opened AND closed (stop-loss exits) |

**Note:** High signals / low orders = risk manager is blocking a lot. Check `logs/trading.log` for the `risk_check_blocked` events to see why.

### Performance panel

| Field | What it means |
|---|---|
| Total PnL | Absolute dollar gain/loss |
| Total return % | PnL as % of starting capital |
| Win rate | % of closed trades that were profitable |
| Sharpe ratio | Risk-adjusted return (>1.0 = good, >2.0 = excellent) |
| Max drawdown | Worst peak-to-trough loss during the run |

**Target ranges for Phase 1 gate:**
- Sharpe > 1.0
- Win rate > 50%
- Max drawdown < 15%
- Positive total PnL

### Last 10 trades

Shows individual entry/exit prices and PnL per trade. Look for:
- Consistent small wins vs. occasional large losses (bad risk/reward)
- All losses clustered in a time period (regime change — strategy not suited to that market)

---

## 5. Tuning Strategy Parameters

The momentum strategy has four main levers. Run the same date range with different values and compare Sharpe + drawdown.

| Parameter | Default | Try lower | Try higher | Effect |
|---|---|---|---|---|
| `--rsi-period` | 14 | 7 (faster, more signals) | 21 (slower, fewer signals) | Sensitivity to momentum |
| `--sma-period` | 20 | 10 | 50 | Trend confirmation window |
| `--oversold` | 30 | 25 (stricter) | 35 (more signals) | LONG signal threshold |
| `--overbought` | 70 | 65 (more signals) | 75 (stricter) | SHORT signal threshold |

### Systematic sweep example

```bash
for rsi in 7 14 21; do
  echo "=== RSI period: $rsi ==="
  bot backtest --symbols AAPL,MSFT --start 2022-01-01 --end 2024-01-01 \
    --rsi-period $rsi --log-level WARNING
done
```

### Risk parameter tuning

Set via `.env` or inline:

```env
MAX_POSITION_FRACTION=0.02   # 2% per trade (never go above 5%)
MAX_DRAWDOWN_FRACTION=0.10   # 10% halt (test with 0.05 and 0.15)
```

---

## 6. Debugging Common Issues

### No signals generated

**Symptoms:** `Signals generated: 0` in the report.

**Check:**
1. Not enough bars? Run `--log-level DEBUG` — look for `insufficient_bars_for_warmup`
2. Signals below `min_confidence`? Look for `signal_below_min_confidence` in logs
3. RSI/SMA never crossing thresholds? Try widening `--oversold 35 --overbought 65`

### All signals blocked by risk manager

**Symptoms:** Signals > 0 but Orders = 0.

**Check:**
```bash
grep "risk_check_blocked" logs/trading.log | head -20
```
Common reasons:
- `kill_switch_active` — drawdown exceeded limit, bot halted
- `max_open_positions_N_reached` — reduce symbols or increase `MAX_OPEN_POSITIONS`
- `confidence_X_below_minimum_Y` — lower `min_signal_confidence` in `RiskLimits`

### Kill switch tripped immediately

**Symptoms:** Bot halts on the first few bars.

**Check:** Starting capital too low relative to position size, or opening trade hits stop-loss immediately.
```bash
grep "kill_switch_tripped" logs/trading.log
```
Try: increase `MAX_DRAWDOWN_FRACTION` to 0.20 for testing, or reduce `MAX_POSITION_FRACTION`.

### Yahoo Finance returns no data

**Symptoms:** `no_data_returned` in logs, bars = 0.

**Check:**
- Symbol spelling: use `BTC-USD` not `BTC/USD` for yfinance
- Date range: yfinance has limits on intraday history (60 days for 1h, 7 days for 1m)
- Rate limiting: wait 30 seconds and retry

### Test failures

```bash
pytest -x -v 2>&1 | head -50
```
- `ImportError`: run `pip install -e ".[dev]"` again
- `AttributeError on trade_count`: ensure you have the latest `metrics.py`

---

## 7. Logs Reference

All logs are in `logs/trading.log` (JSON, one event per line).

### Useful log queries

```bash
# All signals generated today
grep "signal_generated" logs/trading.log | tail -20

# All blocked risk checks
grep "risk_check_blocked" logs/trading.log

# All filled orders
grep "order_filled" logs/trading.log

# Kill switch events
grep "kill_switch" logs/trading.log

# Backtest summary (start + end)
grep "backtest_" logs/trading.log

# Pretty-print a specific event type (requires jq)
grep "risk_check_passed" logs/trading.log | head -5 | jq .
```

### Key log events

| Event | Level | When |
|---|---|---|
| `backtest_started` | INFO | Engine starts |
| `signal_generated` | INFO | Strategy produces a signal |
| `signal_below_min_confidence` | DEBUG | Signal suppressed before risk check |
| `risk_check_passed` | INFO | Signal approved by risk manager |
| `risk_check_blocked` | WARNING | Signal blocked — reason field explains why |
| `order_filled` | INFO | Paper broker executes the order |
| `order_rejected` | ERROR | Broker rejected the order |
| `kill_switch_tripped` | ERROR | Drawdown limit breached, trading halted |
| `backtest_completed` | INFO | Engine finished — summary metrics |
| `position_opened` | DEBUG | Entry recorded |
| `position_closed` | INFO | Exit recorded with PnL |

---

## 8. Phase Readiness Checklist

### Phase 1 → Phase 2 gate

Run this before moving to paper live trading:

```bash
# 1. Tests pass at 80%+ coverage
pytest --cov=bot --cov-fail-under=80

# 2. Backtest over 2 full years shows positive expectancy
bot backtest \
  --symbols AAPL,MSFT,NVDA,BTC-USD \
  --start 2022-01-01 --end 2024-01-01 \
  --capital 10000

# 3. Check: Sharpe > 1.0, Win rate > 50%, Max drawdown < 15%

# 4. No ERROR-level events in the log (except expected kill switch tests)
grep '"level":"error"' logs/trading.log | grep -v "test"
```

### Phase 2 → Phase 3 gate (future)

- 100+ live paper trades with positive expectancy
- Alpaca paper account running continuously for 4 weeks
- No unexpected crashes or missed heartbeats
- All API keys validated and rotated
