"""
In-memory performance tracker.

Records closed trades and computes:
  - Total PnL (absolute + %)
  - Win rate
  - Sharpe ratio (annualised, daily returns)
  - Max drawdown
  - Trade count

Usage:
    tracker = PerformanceTracker(initial_capital=10_000)
    tracker.record_trade(symbol="AAPL", side="long", entry=150.0, exit=157.5, qty=10)
    print(tracker.summary())
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ClosedTrade:
    symbol: str
    side: str          # "long" | "short"
    entry_price: float
    exit_price: float
    qty: float
    entry_time: datetime
    exit_time: datetime
    pnl: float         # absolute dollar PnL after costs


class PerformanceTracker:
    def __init__(self, initial_capital: float) -> None:
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.trades: list[ClosedTrade] = []

        # Track peak for drawdown calculation
        self._peak_capital = initial_capital

        # Daily equity snapshots for Sharpe (date_str -> equity)
        self._daily_equity: dict[str, float] = {}

    # ── Recording ────────────────────────────────────────────────────────────

    def record_trade(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        qty: float,
        entry_time: datetime,
        exit_time: datetime,
        commission: float = 0.0,
    ) -> ClosedTrade:
        if side == "long":
            raw_pnl = (exit_price - entry_price) * qty
        else:
            raw_pnl = (entry_price - exit_price) * qty
        pnl = raw_pnl - commission

        trade = ClosedTrade(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            qty=qty,
            entry_time=entry_time,
            exit_time=exit_time,
            pnl=pnl,
        )
        self.trades.append(trade)
        self.current_capital += pnl

        # Update peak for drawdown
        if self.current_capital > self._peak_capital:
            self._peak_capital = self.current_capital

        # Record daily equity snapshot (last update wins per day)
        day_key = exit_time.strftime("%Y-%m-%d")
        self._daily_equity[day_key] = self.current_capital

        return trade

    def snapshot_equity(self, dt: datetime) -> None:
        """Call at end of each trading day even if no trades occurred."""
        self._daily_equity[dt.strftime("%Y-%m-%d")] = self.current_capital

    # ── Metrics ──────────────────────────────────────────────────────────────

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def total_pnl(self) -> float:
        return self.current_capital - self.initial_capital

    @property
    def total_return_pct(self) -> float:
        return (self.total_pnl / self.initial_capital) * 100

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl > 0)
        return wins / len(self.trades)

    @property
    def max_drawdown_pct(self) -> float:
        """Max peak-to-trough drawdown as a fraction (0.0–1.0)."""
        if not self._daily_equity:
            return 0.0
        equities = list(self._daily_equity.values())
        peak = equities[0]
        max_dd = 0.0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def current_drawdown_pct(self) -> float:
        """Live drawdown from the running peak."""
        if self._peak_capital == 0:
            return 0.0
        return (self._peak_capital - self.current_capital) / self._peak_capital

    @property
    def sharpe_ratio(self) -> float:
        """Annualised Sharpe (assumes 0% risk-free rate for simplicity)."""
        if len(self._daily_equity) < 2:
            return 0.0
        equities = list(self._daily_equity.values())
        daily_returns = [
            (equities[i] - equities[i - 1]) / equities[i - 1]
            for i in range(1, len(equities))
        ]
        n = len(daily_returns)
        mean = sum(daily_returns) / n
        variance = sum((r - mean) ** 2 for r in daily_returns) / n
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(252)  # annualise

    def summary(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "current_capital": round(self.current_capital, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "trade_count": len(self.trades),
            "win_rate": round(self.win_rate * 100, 1),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct * 100, 2),
            "current_drawdown_pct": round(self.current_drawdown_pct * 100, 2),
        }
