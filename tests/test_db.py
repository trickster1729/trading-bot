"""
Tests for bot/db/models.py and bot/db/store.py

All tests use in-memory SQLite (TradeStore.for_testing()) — fast, isolated,
no files left on disk. Each test gets a fresh store via the fixture.

Coverage targets:
- Schema creates cleanly
- start_run / finish_run lifecycle
- save_signal: approved and blocked signals stored correctly
- save_trade: full audit trail persisted
- Query helpers: get_runs, get_run, get_trades, get_signals, win_rate_by_strategy
- run_exists guard
- Multiple runs are independent (no cross-contamination)
- DataFrame queries return correct shape and dtypes
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.db.store import TradeStore
from bot.monitoring.metrics import ClosedTrade, PerformanceTracker
from bot.risk.manager import RiskDecision, RiskResult
from bot.signals.base import AssetClass, Direction, SignalStrength
from tests.conftest import make_signal


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store() -> TradeStore:
    s = TradeStore.for_testing()
    yield s
    s._engine.dispose()   # close SQLite connection, silences ResourceWarning


def _ts(day: int) -> datetime:
    return datetime(2024, 1, day, tzinfo=timezone.utc)


def _make_risk_result(approved: bool, position_size: float = 2.0) -> RiskResult:
    signal = make_signal()
    return RiskResult(
        decision=RiskDecision.APPROVED if approved else RiskDecision.BLOCKED,
        position_size=position_size if approved else 0.0,
        reason="all_checks_passed" if approved else "confidence_too_low",
        signal=signal,
        account_value=10_000.0,
    )


def _make_closed_trade(
    symbol: str = "AAPL",
    side: str = "long",
    entry: float = 100.0,
    exit_: float = 110.0,
    qty: float = 2.0,
) -> ClosedTrade:
    pnl = (exit_ - entry) * qty if side == "long" else (entry - exit_) * qty
    return ClosedTrade(
        symbol=symbol,
        side=side,
        entry_price=entry,
        exit_price=exit_,
        qty=qty,
        entry_time=_ts(1),
        exit_time=_ts(2),
        pnl=pnl,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Schema / initialisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestStoreInit:
    def test_for_testing_creates_in_memory_store(self):
        s = TradeStore.for_testing()
        assert s is not None

    def test_from_url_creates_store(self, tmp_path):
        db_path = tmp_path / "test.db"
        s = TradeStore.from_url(f"sqlite:///{db_path}")
        assert s is not None
        assert db_path.exists()

    def test_tables_created_on_init(self, store):
        # Verify tables exist by running a trivial query — no exception = tables exist
        runs = store.get_runs()
        assert isinstance(runs, list)
        assert len(runs) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Run lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunLifecycle:
    def test_start_run_returns_uuid_string(self, store):
        run_id = store.start_run(
            mode="shadow",
            symbols=["AAPL"],
            strategies=["momentum_rsi_sma"],
            initial_capital=10_000.0,
        )
        assert isinstance(run_id, str)
        assert len(run_id) == 36   # UUID format

    def test_run_exists_after_start(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["momentum"], 10_000.0)
        assert store.run_exists(run_id)

    def test_run_does_not_exist_with_random_id(self, store):
        assert not store.run_exists("00000000-0000-0000-0000-000000000000")

    def test_get_runs_returns_started_run(self, store):
        store.start_run("shadow", ["AAPL"], ["momentum"], 10_000.0)
        runs = store.get_runs()
        assert len(runs) == 1
        assert runs[0]["mode"] == "shadow"
        assert runs[0]["symbols"] == ["AAPL"]

    def test_get_run_by_id(self, store):
        run_id = store.start_run("shadow", ["MSFT"], ["momentum"], 5_000.0)
        run = store.get_run(run_id)
        assert run is not None
        assert run["run_id"] == run_id
        assert run["initial_capital"] == 5_000.0

    def test_get_run_unknown_id_returns_none(self, store):
        assert store.get_run("does-not-exist") is None

    def test_finish_run_updates_performance_metrics(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["momentum"], 10_000.0)
        tracker = PerformanceTracker(initial_capital=10_000.0)
        tracker.record_trade("AAPL", "long", 100, 110, 10, _ts(1), _ts(2))

        store.finish_run(run_id, tracker)

        run = store.get_run(run_id)
        assert run["total_pnl"] == pytest.approx(100.0)
        assert run["trade_count"] == 1
        assert run["win_rate"] == pytest.approx(100.0)

    def test_finish_run_sets_finished_at(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["momentum"], 10_000.0)
        tracker = PerformanceTracker(initial_capital=10_000.0)
        store.finish_run(run_id, tracker)
        run = store.get_run(run_id)
        assert run["finished_at"] is not None

    def test_finish_run_with_unknown_id_does_not_raise(self, store):
        tracker = PerformanceTracker(initial_capital=10_000.0)
        store.finish_run("unknown-id", tracker)   # should log warning, not raise

    def test_multiple_runs_are_independent(self, store):
        id1 = store.start_run("shadow", ["AAPL"], ["m1"], 10_000.0)
        id2 = store.start_run("shadow", ["BTC-USD"], ["m2"], 5_000.0)
        assert id1 != id2
        runs = store.get_runs()
        assert len(runs) == 2

    def test_get_runs_limit_respected(self, store):
        for i in range(5):
            store.start_run("shadow", [f"SYM{i}"], ["m"], 10_000.0)
        runs = store.get_runs(limit=3)
        assert len(runs) == 3

    def test_params_stored_and_retrieved(self, store):
        params = {"rsi_period": 14, "sma_period": 20}
        run_id = store.start_run("shadow", ["AAPL"], ["momentum"], 10_000.0, params=params)
        # params are stored but not exposed in get_run — verify no crash
        assert store.run_exists(run_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Signal persistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalPersistence:
    def test_save_approved_signal_returns_id(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        signal = make_signal(symbol="AAPL", confidence=0.80)
        risk = _make_risk_result(approved=True)
        record_id = store.save_signal(run_id, signal, risk)
        assert isinstance(record_id, str)
        assert len(record_id) == 36

    def test_approved_signal_shows_in_query(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        store.save_signal(run_id, make_signal(symbol="AAPL", confidence=0.80),
                          _make_risk_result(approved=True))
        df = store.get_signals(run_id=run_id)
        assert len(df) == 1
        assert df.iloc[0]["was_approved"] == True
        assert df.iloc[0]["symbol"] == "AAPL"

    def test_blocked_signal_stores_reason(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        store.save_signal(run_id, make_signal(symbol="AAPL", confidence=0.40),
                          _make_risk_result(approved=False))
        df = store.get_signals(run_id=run_id)
        assert df.iloc[0]["was_approved"] == False
        assert df.iloc[0]["block_reason"] == "confidence_too_low"

    def test_approved_only_filter(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        store.save_signal(run_id, make_signal(confidence=0.80), _make_risk_result(True))
        store.save_signal(run_id, make_signal(confidence=0.40), _make_risk_result(False))
        df = store.get_signals(run_id=run_id, approved_only=True)
        assert len(df) == 1
        assert df.iloc[0]["was_approved"] == True

    def test_signal_position_size_stored(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        store.save_signal(run_id, make_signal(), _make_risk_result(approved=True, position_size=5.0))
        df = store.get_signals(run_id=run_id)
        assert df.iloc[0]["position_size"] == pytest.approx(5.0)

    def test_signals_filtered_by_symbol(self, store):
        run_id = store.start_run("shadow", ["AAPL", "MSFT"], ["m"], 10_000.0)
        store.save_signal(run_id, make_signal(symbol="AAPL"), _make_risk_result(True))
        store.save_signal(run_id, make_signal(symbol="MSFT"), _make_risk_result(True))
        df = store.get_signals(run_id=run_id, symbol="AAPL")
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "AAPL"

    def test_empty_signals_returns_empty_dataframe(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        df = store.get_signals(run_id=run_id)
        assert df.empty

    def test_signal_confidence_and_direction_stored(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        signal = make_signal(direction=Direction.LONG, confidence=0.75)
        store.save_signal(run_id, signal, _make_risk_result(True))
        df = store.get_signals(run_id=run_id)
        assert df.iloc[0]["confidence"] == pytest.approx(0.75)
        assert df.iloc[0]["direction"] == "long"


# ═══════════════════════════════════════════════════════════════════════════════
# Trade persistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestTradePersistence:
    def test_save_trade_returns_id(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        trade = _make_closed_trade()
        record_id = store.save_trade(run_id, trade, strategy="momentum_rsi_sma")
        assert isinstance(record_id, str)

    def test_winning_trade_stored_correctly(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        trade = _make_closed_trade(entry=100.0, exit_=110.0, qty=2.0)  # PnL=$20
        store.save_trade(run_id, trade, strategy="momentum_rsi_sma",
                         signal_confidence=0.80, exit_reason="signal")
        df = store.get_trades(run_id=run_id)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["symbol"] == "AAPL"
        assert row["pnl"] == pytest.approx(20.0)
        assert row["is_winner"] == True
        assert row["exit_reason"] == "signal"
        assert row["signal_confidence"] == pytest.approx(0.80)

    def test_losing_trade_is_winner_false(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        trade = _make_closed_trade(entry=110.0, exit_=100.0, qty=2.0)  # PnL=-$20
        store.save_trade(run_id, trade)
        df = store.get_trades(run_id=run_id)
        assert df.iloc[0]["is_winner"] == False

    def test_pnl_pct_calculated_on_retrieval(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        # Entry cost = 100 * 2 = $200, PnL = $20 → 10%
        trade = _make_closed_trade(entry=100.0, exit_=110.0, qty=2.0)
        store.save_trade(run_id, trade)
        df = store.get_trades(run_id=run_id)
        assert df.iloc[0]["pnl_pct"] == pytest.approx(10.0)

    def test_trades_filtered_by_run_id(self, store):
        run1 = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        run2 = store.start_run("shadow", ["MSFT"], ["m"], 10_000.0)
        store.save_trade(run1, _make_closed_trade(symbol="AAPL"))
        store.save_trade(run2, _make_closed_trade(symbol="MSFT"))
        df = store.get_trades(run_id=run1)
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "AAPL"

    def test_trades_filtered_by_symbol(self, store):
        run_id = store.start_run("shadow", ["AAPL", "MSFT"], ["m"], 10_000.0)
        store.save_trade(run_id, _make_closed_trade(symbol="AAPL"))
        store.save_trade(run_id, _make_closed_trade(symbol="MSFT"))
        df = store.get_trades(run_id=run_id, symbol="AAPL")
        assert len(df) == 1

    def test_empty_trades_returns_empty_dataframe(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        df = store.get_trades(run_id=run_id)
        assert df.empty

    def test_multiple_trades_all_retrieved(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        for i in range(5):
            store.save_trade(run_id, _make_closed_trade(entry=100.0 + i, exit_=110.0 + i))
        df = store.get_trades(run_id=run_id)
        assert len(df) == 5


# ═══════════════════════════════════════════════════════════════════════════════
# Analytics queries
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnalyticsQueries:
    def test_win_rate_by_strategy_single_strategy(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["momentum"], 10_000.0)
        store.save_trade(run_id, _make_closed_trade(entry=100, exit_=110), strategy="momentum")  # win
        store.save_trade(run_id, _make_closed_trade(entry=110, exit_=100), strategy="momentum")  # loss
        df = store.get_win_rate_by_strategy(run_id=run_id)
        assert len(df) == 1
        assert df.iloc[0]["strategy"] == "momentum"
        assert df.iloc[0]["trade_count"] == 2
        assert df.iloc[0]["win_rate"] == pytest.approx(0.5)

    def test_win_rate_by_strategy_multiple_strategies(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m1", "m2"], 10_000.0)
        store.save_trade(run_id, _make_closed_trade(entry=100, exit_=110), strategy="m1")
        store.save_trade(run_id, _make_closed_trade(entry=100, exit_=110), strategy="m2")
        store.save_trade(run_id, _make_closed_trade(entry=110, exit_=100), strategy="m2")
        df = store.get_win_rate_by_strategy(run_id=run_id)
        assert len(df) == 2
        m1 = df[df["strategy"] == "m1"].iloc[0]
        m2 = df[df["strategy"] == "m2"].iloc[0]
        assert m1["win_rate"] == pytest.approx(1.0)
        assert m2["win_rate"] == pytest.approx(0.5)

    def test_win_rate_by_strategy_no_trades_returns_empty(self, store):
        run_id = store.start_run("shadow", ["AAPL"], ["m"], 10_000.0)
        df = store.get_win_rate_by_strategy(run_id=run_id)
        assert df.empty
