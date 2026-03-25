"""
Database models — SQLAlchemy declarative ORM.

Three tables:
  BacktestRun   — one row per backtest / live session (the "run" container)
  TradeRecord   — one row per closed trade (full audit trail)
  SignalRecord  — one row per signal generated (approved and blocked)

Design for scale
----------------
- All IDs are UUIDs (string) — safe across distributed systems, no integer
  collisions when we shard or replicate in Phase 4.
- `params` / `signal_metadata` stored as JSON text — flexible for future
  strategy types without schema migrations.
- Switching to PostgreSQL: change the DB_URL in .env from
    sqlite:///trading.db
  to
    postgresql+psycopg2://user:pass@host/db
  SQLAlchemy handles the rest. JSON columns become native JSONB in Postgres.

Usage:
    from bot.db.models import Base
    from sqlalchemy import create_engine
    engine = create_engine("sqlite:///trading.db")
    Base.metadata.create_all(engine)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ── BacktestRun ───────────────────────────────────────────────────────────────

class BacktestRun(Base):
    """
    One row per backtest execution or live trading session.

    Acts as the parent record — all TradeRecords and SignalRecords reference
    a run_id so you can query "everything that happened in run X".
    """
    __tablename__ = "backtest_runs"

    run_id:         Mapped[str]      = mapped_column(String(36), primary_key=True, default=_uuid)
    mode:           Mapped[str]      = mapped_column(String(16))   # shadow | paper | live
    symbols:        Mapped[Any]      = mapped_column(JSON)         # ["AAPL", "MSFT"]
    strategies:     Mapped[Any]      = mapped_column(JSON)         # ["momentum_rsi_sma"]
    initial_capital:Mapped[float]    = mapped_column(Float)
    final_capital:  Mapped[float]    = mapped_column(Float, default=0.0)
    total_pnl:      Mapped[float]    = mapped_column(Float, default=0.0)
    total_return_pct: Mapped[float]  = mapped_column(Float, default=0.0)
    trade_count:    Mapped[int]      = mapped_column(Integer, default=0)
    win_rate:       Mapped[float]    = mapped_column(Float, default=0.0)
    sharpe_ratio:   Mapped[float]    = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float]  = mapped_column(Float, default=0.0)
    total_bars:     Mapped[int]      = mapped_column(Integer, default=0)
    signals_generated: Mapped[int]   = mapped_column(Integer, default=0)
    orders_filled:  Mapped[int]      = mapped_column(Integer, default=0)
    # Strategy params snapshot — so you can reproduce the exact run later
    params:         Mapped[Any]      = mapped_column(JSON, default=dict)
    backtest_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backtest_end:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"BacktestRun(run_id={self.run_id!r}, mode={self.mode!r}, "
            f"symbols={self.symbols}, pnl={self.total_pnl:.2f})"
        )


# ── TradeRecord ───────────────────────────────────────────────────────────────

class TradeRecord(Base):
    """
    One row per closed trade — the core audit log.

    Every trade decision is stored with enough context to answer:
    "Why did the bot buy AAPL on Jan 15? What signal triggered it?
    What was the RSI? What risk checks passed? What was the outcome?"

    This is the table you'll analyse to improve strategy parameters,
    and the data source for the research paper's empirical results section.
    """
    __tablename__ = "trade_records"

    id:             Mapped[str]   = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id:         Mapped[str]   = mapped_column(String(36), index=True)
    symbol:         Mapped[str]   = mapped_column(String(20), index=True)
    side:           Mapped[str]   = mapped_column(String(8))    # long | short
    strategy:       Mapped[str]   = mapped_column(String(64))
    asset_class:    Mapped[str]   = mapped_column(String(16))   # equity | crypto | option
    entry_price:    Mapped[float] = mapped_column(Float)
    exit_price:     Mapped[float] = mapped_column(Float)
    qty:            Mapped[float] = mapped_column(Float)
    pnl:            Mapped[float] = mapped_column(Float)        # after commission
    commission:     Mapped[float] = mapped_column(Float, default=0.0)
    entry_time:     Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_time:      Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Signal context — so you can trace the full reasoning chain
    signal_confidence:  Mapped[float]    = mapped_column(Float)
    signal_direction:   Mapped[str]      = mapped_column(String(8))
    signal_strength:    Mapped[str]      = mapped_column(String(8))
    signal_metadata:    Mapped[Any]      = mapped_column(JSON, default=dict)
    # Exit reason — 'stop_loss' | 'take_profit' | 'signal' | 'end_of_run'
    exit_reason:    Mapped[str]   = mapped_column(String(32), default="unknown")
    created_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    @property
    def pnl_pct(self) -> float:
        """PnL as % of entry cost."""
        cost = self.entry_price * self.qty
        return (self.pnl / cost * 100) if cost > 0 else 0.0

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    def __repr__(self) -> str:
        return (
            f"TradeRecord(symbol={self.symbol!r}, side={self.side!r}, "
            f"pnl={self.pnl:.2f}, strategy={self.strategy!r})"
        )


# ── SignalRecord ──────────────────────────────────────────────────────────────

class SignalRecord(Base):
    """
    One row per signal generated — including ones the risk manager blocked.

    Logging blocked signals is as important as logging filled ones.
    It tells you:
    - How often your strategy fires but gets gated by risk
    - Whether the risk limits are too tight (blocking good signals)
    - Signal quality trends over time (is confidence improving?)
    """
    __tablename__ = "signal_records"

    id:             Mapped[str]   = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id:         Mapped[str]   = mapped_column(String(36), index=True)
    symbol:         Mapped[str]   = mapped_column(String(20), index=True)
    strategy:       Mapped[str]   = mapped_column(String(64))
    asset_class:    Mapped[str]   = mapped_column(String(16))
    direction:      Mapped[str]   = mapped_column(String(8))
    confidence:     Mapped[float] = mapped_column(Float)
    strength:       Mapped[str]   = mapped_column(String(8))
    price:          Mapped[float] = mapped_column(Float)
    bar_timestamp:  Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Risk manager outcome
    was_approved:    Mapped[bool]  = mapped_column(Boolean)
    block_reason:    Mapped[str | None] = mapped_column(Text, nullable=True)
    # Position size assigned (0 if blocked)
    position_size:   Mapped[float] = mapped_column(Float, default=0.0)
    signal_metadata: Mapped[Any]   = mapped_column(JSON, default=dict)
    created_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    def __repr__(self) -> str:
        status = "approved" if self.was_approved else f"blocked:{self.block_reason}"
        return (
            f"SignalRecord(symbol={self.symbol!r}, direction={self.direction!r}, "
            f"confidence={self.confidence:.2f}, status={status!r})"
        )
