# Trading Bot — Developer Makefile
#
# Run everything from your laptop with a single command.
# No Docker needed until Phase 4.
#
# Quick reference:
#   make install       install all deps including dev tools
#   make test          run tests with coverage (fails if <80%)
#   make backtest      run a default backtest (AAPL + MSFT, 2023)
#   make backtest SYMBOLS=BTC-USD,ETH-USD START=2022-01-01 END=2023-01-01
#   make clean         remove logs, cache, pycache

.PHONY: install test test-fast lint backtest clean help

# ── Defaults (override on command line) ──────────────────────────────────────
SYMBOLS    ?= AAPL,MSFT
START      ?= 2023-01-01
END        ?= 2024-01-01
CAPITAL    ?= 10000
INTERVAL   ?= 1d
LOG_LEVEL  ?= INFO

# ── Setup ─────────────────────────────────────────────────────────────────────

install:
	pip install -e ".[dev]"

# ── Testing ───────────────────────────────────────────────────────────────────

test:
	pytest --cov=bot --cov-report=term-missing --cov-fail-under=80

test-fast:
	pytest -x -q

test-watch:
	# Requires: pip install pytest-watch
	ptw -- --tb=short -q

# ── Lint ──────────────────────────────────────────────────────────────────────

lint:
	ruff check bot tests
	ruff format --check bot tests

lint-fix:
	ruff check --fix bot tests
	ruff format bot tests

# ── Running the bot ───────────────────────────────────────────────────────────

backtest:
	bot backtest \
		--symbols $(SYMBOLS) \
		--start $(START) \
		--end $(END) \
		--capital $(CAPITAL) \
		--interval $(INTERVAL) \
		--log-level $(LOG_LEVEL)

# Backtest with debug logging — see every signal calculation
backtest-debug:
	$(MAKE) backtest LOG_LEVEL=DEBUG

# Quick smoke test: one symbol, one month — verifies the pipeline runs
smoke:
	bot backtest --symbols AAPL --start 2023-06-01 --end 2023-07-01 --capital 10000

# ── Database ──────────────────────────────────────────────────────────────────

db-shell:
	# Open SQLite shell on the trading database
	sqlite3 trading.db

db-trades:
	sqlite3 trading.db "SELECT symbol, side, round(entry_price,2), round(exit_price,2), round(pnl,2), exit_reason, strategy FROM trade_records ORDER BY exit_time DESC LIMIT 20;"

db-runs:
	sqlite3 trading.db "SELECT run_id, mode, symbols, round(total_pnl,2), win_rate, sharpe_ratio, started_at FROM backtest_runs ORDER BY started_at DESC LIMIT 10;"

db-signals:
	sqlite3 trading.db "SELECT symbol, direction, round(confidence,3), was_approved, block_reason FROM signal_records ORDER BY created_at DESC LIMIT 20;"

# ── Logs ──────────────────────────────────────────────────────────────────────

logs:
	tail -f logs/trading.log

logs-errors:
	grep '"level":"error"' logs/trading.log | tail -20

logs-trades:
	grep 'order_filled\|position_closed' logs/trading.log | tail -20

logs-signals:
	grep 'signal_generated\|risk_check_blocked' logs/trading.log | tail -20

# ── Cleanup ───────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .coverage htmlcov
	rm -f logs/*.log

clean-db:
	rm -f trading.db
	@echo "Database deleted. Next backtest run will recreate it."

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "Trading Bot — available commands:"
	@echo ""
	@echo "  make install                install deps"
	@echo "  make test                   run tests (80% coverage gate)"
	@echo "  make test-fast              run tests, stop on first failure"
	@echo "  make lint                   check code style"
	@echo "  make lint-fix               auto-fix style issues"
	@echo ""
	@echo "  make backtest               run default backtest (AAPL,MSFT 2023)"
	@echo "  make backtest SYMBOLS=X     backtest with custom symbols"
	@echo "  make backtest-debug         backtest with DEBUG logging"
	@echo "  make smoke                  quick 1-month pipeline smoke test"
	@echo ""
	@echo "  make db-trades              show last 20 trades from DB"
	@echo "  make db-runs                show last 10 backtest runs"
	@echo "  make db-signals             show last 20 signals"
	@echo "  make db-shell               open SQLite shell"
	@echo ""
	@echo "  make logs                   tail live log file"
	@echo "  make logs-errors            show recent errors"
	@echo "  make logs-trades            show recent fills + closed positions"
	@echo ""
	@echo "  make clean                  remove cache + logs"
	@echo "  make clean-db               delete trading.db"
	@echo ""
	@echo "Overridable defaults:"
	@echo "  SYMBOLS=$(SYMBOLS)  START=$(START)  END=$(END)"
	@echo "  CAPITAL=$(CAPITAL)  INTERVAL=$(INTERVAL)  LOG_LEVEL=$(LOG_LEVEL)"
	@echo ""
