"""
TradeStore — the single interface between the bot and the database.

All DB writes go through here. Nothing else in the codebase imports
SQLAlchemy directly — only this module does. This keeps the DB technology
swappable (SQLite → PostgreSQL → TimescaleDB) without touching business logic.

Usage:
    store = TradeStore.from_url("sqlite:///trading.db")   # Phase 1
    store = TradeStore.from_url("postgresql://...")         # Phase 3+

    run_id = store.start_run(mode="shadow", symbols=["AAPL"], ...)
    store.save_signal(run_id, signal, risk_result)
    store.save_trade(run_id, trade_record)
    store.finish_run(run_id, summary)

Query helpers return plain dicts or dataframes so callers don't need
to know anything about SQLAlchemy.

Phase 3+ extension points:
- Add async session support (asyncpg driver) for the live loop
- Add a read-replica session for reporting queries without locking writes
- Add bulk insert for signal records (the live loop will emit many)
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from bot.db.models import BacktestRun, Base, SignalRecord, TradeRecord
from bot.monitoring.logger import get_logger
from bot.monitoring.metrics import ClosedTrade, PerformanceTracker
from bot.risk.manager import RiskResult
from bot.signals.base import Signal

log = get_logger(__name__)


class TradeStore:
    """
    Thin persistence layer over SQLAlchemy.

    All methods are synchronous (sqlite3 is thread-safe in serialised mode).
    Phase 3 will add async variants when the live loop needs non-blocking writes.
    """

    def __init__(self, db_url: str) -> None:
        self._engine = create_engine(
            db_url,
            # For SQLite: enable WAL mode for better concurrent read/write
            connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
        )
        self._Session = sessionmaker(bind=self._engine, expire_on_commit=False)
        Base.metadata.create_all(self._engine)
        log.info("db_initialised", db_url=db_url)

    @classmethod
    def from_url(cls, db_url: str) -> "TradeStore":
        """Preferred constructor — explicit about the connection string."""
        return cls(db_url)

    @classmethod
    def for_testing(cls) -> "TradeStore":
        """In-memory SQLite — fast, isolated, no files left behind."""
        return cls("sqlite:///:memory:")

    # ── Session context manager ───────────────────────────────────────────────

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        session = self._Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Run lifecycle ─────────────────────────────────────────────────────────

    def start_run(
        self,
        mode: str,
        symbols: list[str],
        strategies: list[str],
        initial_capital: float,
        params: dict[str, Any] | None = None,
        backtest_start: datetime | None = None,
        backtest_end: datetime | None = None,
    ) -> str:
        """
        Create a new BacktestRun record and return its run_id.
        Call this before any trades or signals are saved.
        """
        run_id = str(uuid.uuid4())
        with self._session() as s:
            run = BacktestRun(
                run_id=run_id,
                mode=mode,
                symbols=symbols,
                strategies=strategies,
                initial_capital=initial_capital,
                params=params or {},
                backtest_start=backtest_start,
                backtest_end=backtest_end,
            )
            s.add(run)

        log.info(
            "run_started",
            run_id=run_id,
            mode=mode,
            symbols=symbols,
            initial_capital=initial_capital,
        )
        return run_id

    def finish_run(self, run_id: str, tracker: PerformanceTracker, **extra: Any) -> None:
        """
        Update the BacktestRun with final performance metrics.
        Call this after the engine completes.
        """
        summary = tracker.summary()
        with self._session() as s:
            run = s.get(BacktestRun, run_id)
            if run is None:
                log.warning("finish_run_not_found", run_id=run_id)
                return
            run.final_capital    = summary["current_capital"]
            run.total_pnl        = summary["total_pnl"]
            run.total_return_pct = summary["total_return_pct"]
            run.trade_count      = summary["trade_count"]
            run.win_rate         = summary["win_rate"]
            run.sharpe_ratio     = summary["sharpe_ratio"]
            run.max_drawdown_pct = summary["max_drawdown_pct"]
            run.finished_at      = datetime.now(tz=timezone.utc)
            for k, v in extra.items():
                if hasattr(run, k):
                    setattr(run, k, v)
            s.add(run)

        log.info(
            "run_finished",
            run_id=run_id,
            total_pnl=summary["total_pnl"],
            win_rate=summary["win_rate"],
            sharpe_ratio=summary["sharpe_ratio"],
        )

    # ── Signal persistence ────────────────────────────────────────────────────

    def save_signal(self, run_id: str, signal: Signal, risk_result: RiskResult) -> str:
        """
        Persist a signal and the risk manager's decision about it.
        Returns the saved record's ID.
        """
        record_id = str(uuid.uuid4())
        with self._session() as s:
            record = SignalRecord(
                id=record_id,
                run_id=run_id,
                symbol=signal.symbol,
                strategy=signal.strategy,
                asset_class=signal.asset_class.value,
                direction=signal.direction.value,
                confidence=signal.confidence,
                strength=signal.strength.value,
                price=signal.price,
                bar_timestamp=signal.timestamp,
                was_approved=risk_result.approved,
                block_reason=None if risk_result.approved else risk_result.reason,
                position_size=risk_result.position_size,
                signal_metadata=signal.metadata,
            )
            s.add(record)
        return record_id

    # ── Trade persistence ─────────────────────────────────────────────────────

    def save_trade(
        self,
        run_id: str,
        trade: ClosedTrade,
        strategy: str = "",
        signal_confidence: float = 0.0,
        signal_direction: str = "long",
        signal_strength: str = "medium",
        signal_metadata: dict[str, Any] | None = None,
        exit_reason: str = "unknown",
        asset_class: str = "equity",
    ) -> str:
        """
        Persist a closed trade from PerformanceTracker.
        Returns the saved record's ID.

        The extra arguments carry the signal context that PerformanceTracker
        doesn't store itself — pass them from the engine when calling record_trade.
        """
        record_id = str(uuid.uuid4())
        with self._session() as s:
            record = TradeRecord(
                id=record_id,
                run_id=run_id,
                symbol=trade.symbol,
                side=trade.side,
                strategy=strategy,
                asset_class=asset_class,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                qty=trade.qty,
                pnl=trade.pnl,
                commission=trade.pnl - ((trade.exit_price - trade.entry_price) * trade.qty)
                    if trade.side == "long" else 0.0,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                signal_confidence=signal_confidence,
                signal_direction=signal_direction,
                signal_strength=signal_strength,
                signal_metadata=signal_metadata or {},
                exit_reason=exit_reason,
            )
            s.add(record)
        return record_id

    # ── Query helpers ─────────────────────────────────────────────────────────

    def get_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent backtest runs as plain dicts."""
        with self._session() as s:
            stmt = select(BacktestRun).order_by(BacktestRun.started_at.desc()).limit(limit)
            rows = s.scalars(stmt).all()
            return [_run_to_dict(r) for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return a single run by ID."""
        with self._session() as s:
            run = s.get(BacktestRun, run_id)
            return _run_to_dict(run) if run else None

    def get_trades(
        self,
        run_id: str | None = None,
        symbol: str | None = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        Return trades as a DataFrame for analysis.
        Optionally filter by run_id or symbol.
        """
        with self._session() as s:
            stmt = select(TradeRecord)
            if run_id:
                stmt = stmt.where(TradeRecord.run_id == run_id)
            if symbol:
                stmt = stmt.where(TradeRecord.symbol == symbol)
            stmt = stmt.order_by(TradeRecord.exit_time.desc()).limit(limit)
            rows = s.scalars(stmt).all()
            if not rows:
                return pd.DataFrame()
            return pd.DataFrame([_trade_to_dict(r) for r in rows])

    def get_signals(
        self,
        run_id: str | None = None,
        symbol: str | None = None,
        approved_only: bool = False,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Return signals as a DataFrame. Useful for signal quality analysis."""
        with self._session() as s:
            stmt = select(SignalRecord)
            if run_id:
                stmt = stmt.where(SignalRecord.run_id == run_id)
            if symbol:
                stmt = stmt.where(SignalRecord.symbol == symbol)
            if approved_only:
                stmt = stmt.where(SignalRecord.was_approved.is_(True))
            stmt = stmt.order_by(SignalRecord.bar_timestamp.desc()).limit(limit)
            rows = s.scalars(stmt).all()
            if not rows:
                return pd.DataFrame()
            return pd.DataFrame([_signal_to_dict(r) for r in rows])

    def get_win_rate_by_strategy(self, run_id: str | None = None) -> pd.DataFrame:
        """Strategy-level win rate breakdown — useful for comparing strategies."""
        trades_df = self.get_trades(run_id=run_id)
        if trades_df.empty:
            return pd.DataFrame()
        return (
            trades_df.groupby("strategy")
            .agg(
                trade_count=("pnl", "count"),
                win_rate=("pnl", lambda x: (x > 0).mean()),
                total_pnl=("pnl", "sum"),
                avg_pnl=("pnl", "mean"),
            )
            .reset_index()
        )

    def run_exists(self, run_id: str) -> bool:
        with self._session() as s:
            return s.get(BacktestRun, run_id) is not None


# ── Serialisers ───────────────────────────────────────────────────────────────

def _run_to_dict(run: BacktestRun) -> dict[str, Any]:
    return {
        "run_id":           run.run_id,
        "mode":             run.mode,
        "symbols":          run.symbols,
        "strategies":       run.strategies,
        "initial_capital":  run.initial_capital,
        "final_capital":    run.final_capital,
        "total_pnl":        run.total_pnl,
        "total_return_pct": run.total_return_pct,
        "trade_count":      run.trade_count,
        "win_rate":         run.win_rate,
        "sharpe_ratio":     run.sharpe_ratio,
        "max_drawdown_pct": run.max_drawdown_pct,
        "started_at":       run.started_at.isoformat() if run.started_at else None,
        "finished_at":      run.finished_at.isoformat() if run.finished_at else None,
    }


def _trade_to_dict(trade: TradeRecord) -> dict[str, Any]:
    return {
        "id":                trade.id,
        "run_id":            trade.run_id,
        "symbol":            trade.symbol,
        "side":              trade.side,
        "strategy":          trade.strategy,
        "asset_class":       trade.asset_class,
        "entry_price":       trade.entry_price,
        "exit_price":        trade.exit_price,
        "qty":               trade.qty,
        "pnl":               trade.pnl,
        "pnl_pct":           trade.pnl_pct,
        "commission":        trade.commission,
        "entry_time":        trade.entry_time.isoformat(),
        "exit_time":         trade.exit_time.isoformat(),
        "signal_confidence": trade.signal_confidence,
        "signal_direction":  trade.signal_direction,
        "signal_strength":   trade.signal_strength,
        "exit_reason":       trade.exit_reason,
        "is_winner":         trade.is_winner,
    }


def _signal_to_dict(sig: SignalRecord) -> dict[str, Any]:
    return {
        "id":           sig.id,
        "run_id":       sig.run_id,
        "symbol":       sig.symbol,
        "strategy":     sig.strategy,
        "direction":    sig.direction,
        "confidence":   sig.confidence,
        "strength":     sig.strength,
        "price":        sig.price,
        "bar_timestamp": sig.bar_timestamp.isoformat(),
        "was_approved": sig.was_approved,
        "block_reason": sig.block_reason,
        "position_size": sig.position_size,
    }
