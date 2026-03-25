"""
Backtest engine — historical bar-by-bar replay.

Iterates over a historical OHLCV dataset one bar at a time, feeding each bar
to the strategy, passing signals through the risk manager, and submitting
approved orders to the paper broker. Every decision is logged.

Design for scale
----------------
- The engine is strategy-agnostic: it accepts any Strategy subclass.
- Multiple strategies can be passed and run in parallel per bar (ensemble, Phase 3).
- Multiple symbols are supported: each symbol gets its own bar window.
- The engine is stateless between runs: state lives in tracker + broker.
- Adding live replay is a matter of swapping the data source and removing
  the outer loop — the signal/risk/execution pipeline is identical.

Reference: backtesting.py strategy/backtest pattern.

Usage (see bot/cli.py):
    engine = BacktestEngine(
        strategies=[MomentumStrategy()],
        loader=YahooLoader(),
        broker=PaperBroker(),
        tracker=PerformanceTracker(initial_capital=10_000),
        risk=RiskManager(limits=RiskLimits(), tracker=tracker),
    )
    result = engine.run(
        symbols=["AAPL", "MSFT"],
        start=datetime(2023, 1, 1),
        end=datetime(2024, 1, 1),
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from bot.data.base import DataLoader
from bot.db.store import TradeStore
from bot.execution.base import Broker
from bot.execution.paper import PaperBroker
from bot.monitoring.logger import get_logger
from bot.monitoring.metrics import PerformanceTracker
from bot.risk.manager import RiskManager
from bot.signals.base import Direction, Signal, Strategy

log = get_logger(__name__)


# ── Result container ──────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    """Summary of a completed backtest run."""
    symbols:       list[str]
    start:         datetime
    end:           datetime
    strategy_names: list[str]
    total_bars:    int
    signals_generated: int
    orders_submitted:  int
    orders_filled:     int
    performance:   dict       = field(default_factory=dict)


# ── Engine ────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    Historical replay engine.

    Args:
        strategies : one or more Strategy instances to run per bar
        loader     : DataLoader to fetch historical bars
        broker     : Broker to submit orders to (almost always PaperBroker)
        tracker    : PerformanceTracker to record closed trades
        risk       : RiskManager to gate signals before execution
        window_size: number of bars to pass to the strategy on each step
                     (rolling window — strategy sees the last N bars)
    """

    def __init__(
        self,
        strategies: list[Strategy],
        loader: DataLoader,
        broker: Broker,
        tracker: PerformanceTracker,
        risk: RiskManager,
        window_size: int = 60,
        store: TradeStore | None = None,
    ) -> None:
        self.strategies  = strategies
        self.loader      = loader
        self.broker      = broker
        self.tracker     = tracker
        self.risk        = risk
        self.window_size = window_size
        self.store       = store   # None = no persistence (e.g. fast unit tests)

        # Track open positions: symbol → number of open positions
        # In Phase 1 this is maintained here; in Phase 2+ it syncs from the broker.
        self._open_positions: dict[str, int] = {}
        # Track entry price and originating signal per symbol for PnL + audit trail
        self._entry_prices:  dict[str, float]  = {}
        self._entry_signals: dict[str, Signal] = {}
        self._run_id: str | None = None

    # ── Main run loop ─────────────────────────────────────────────────────────

    def run(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> BacktestResult:
        """
        Run the backtest over `symbols` between `start` and `end`.

        For each bar of each symbol, each strategy is called with a rolling
        window of bars ending at that bar. Signals are risk-checked and
        approved ones are submitted to the broker.

        Args:
            symbols  : list of ticker symbols (e.g. ["AAPL", "BTC-USD"])
            start    : backtest start date (inclusive)
            end      : backtest end date (exclusive)
            interval : bar interval (Yahoo Finance notation: 1d, 1h, 5m)
        """
        strategy_names = [s.name for s in self.strategies]
        log.info(
            "backtest_started",
            symbols=symbols,
            start=start.date().isoformat(),
            end=end.date().isoformat(),
            interval=interval,
            strategies=strategy_names,
            window_size=self.window_size,
        )

        # Open a persistent run record if a store is configured
        if self.store:
            self._run_id = self.store.start_run(
                mode="shadow",
                symbols=symbols,
                strategies=strategy_names,
                initial_capital=self.tracker.initial_capital,
                params={s.name: s.params for s in self.strategies},
                backtest_start=start,
                backtest_end=end,
            )

        total_bars         = 0
        signals_generated  = 0
        orders_submitted   = 0
        orders_filled      = 0

        for symbol in symbols:
            bars_df = self._fetch(symbol, start, end, interval)
            if bars_df is None or bars_df.empty:
                continue

            s_bars, s_sigs, s_orders, s_fills = self._replay_symbol(symbol, bars_df)
            total_bars        += s_bars
            signals_generated += s_sigs
            orders_submitted  += s_orders
            orders_filled     += s_fills

            if not bars_df.empty:
                self.tracker.snapshot_equity(bars_df.index[-1].to_pydatetime())

        performance = self.tracker.summary()

        if self.store and self._run_id:
            self.store.finish_run(
                self._run_id,
                self.tracker,
                total_bars=total_bars,
                signals_generated=signals_generated,
                orders_filled=orders_filled,
            )

        log.info(
            "backtest_completed",
            symbols=symbols,
            total_bars=total_bars,
            signals_generated=signals_generated,
            orders_submitted=orders_submitted,
            orders_filled=orders_filled,
            **performance,
        )

        return BacktestResult(
            symbols=symbols,
            start=start,
            end=end,
            strategy_names=strategy_names,
            total_bars=total_bars,
            signals_generated=signals_generated,
            orders_submitted=orders_submitted,
            orders_filled=orders_filled,
            performance=performance,
        )

    # ── Per-symbol replay ─────────────────────────────────────────────────────

    def _replay_symbol(
        self, symbol: str, bars_df: pd.DataFrame
    ) -> tuple[int, int, int, int]:
        """
        Step through bars for a single symbol.
        Returns (bars_processed, signals, orders_submitted, orders_filled).
        """
        total_bars        = 0
        signals_generated = 0
        orders_submitted  = 0
        orders_filled     = 0

        min_warmup = max((s.warm_up_bars() for s in self.strategies), default=0)

        for i in range(min_warmup, len(bars_df)):
            # Rolling window: strategy sees up to `window_size` bars ending at i
            window_start = max(0, i - self.window_size + 1)
            window = bars_df.iloc[window_start : i + 1]
            current_bar = bars_df.iloc[i]
            current_price = float(current_bar["close"])
            bar_ts = bars_df.index[i].to_pydatetime()

            total_bars += 1

            # ── Check for exit conditions on open positions ────────────────
            self._check_exits(symbol, current_price, bar_ts)

            # ── Generate signals from each strategy ───────────────────────
            for strategy in self.strategies:
                signals = strategy.generate_signals(window, symbol)
                signals_generated += len(signals)

                for signal in signals:
                    if self.risk.is_halted:
                        log.warning(
                            "signal_skipped_risk_halted",
                            symbol=symbol,
                            strategy=strategy.name,
                            bar_ts=bar_ts.isoformat(),
                        )
                        break

                    risk_result = self.risk.evaluate(
                        signal=signal,
                        open_positions=self._open_positions,
                        price=current_price,
                    )

                    # Persist every signal + risk decision (approved and blocked)
                    if self.store and self._run_id:
                        self.store.save_signal(self._run_id, signal, risk_result)

                    if not risk_result.approved:
                        continue

                    order = self.broker.submit_order(
                        signal=signal,
                        quantity=risk_result.position_size,
                    )
                    orders_submitted += 1

                    if order.filled:
                        orders_filled += 1
                        self._record_entry(symbol, order.fill_price, order.filled_qty, signal)

            # Snapshot equity daily
            self.tracker.snapshot_equity(bar_ts)

        return total_bars, signals_generated, orders_submitted, orders_filled

    # ── Entry / exit tracking ─────────────────────────────────────────────────

    def _record_entry(
        self, symbol: str, price: float, qty: float, signal: Signal
    ) -> None:
        """Record an entry so we can compute PnL on exit."""
        self._open_positions[symbol] = self._open_positions.get(symbol, 0) + 1
        self._entry_prices[symbol]   = price
        self._entry_signals[symbol]  = signal  # kept for trade audit trail on exit
        log.debug(
            "position_opened",
            symbol=symbol,
            entry_price=price,
            qty=qty,
            direction=signal.direction.value,
            strategy=signal.strategy,
        )

    def _check_exits(self, symbol: str, current_price: float, bar_ts: datetime) -> None:
        """
        Simple exit logic for Phase 1: close position if stop_loss is hit.
        Phase 2+ will add: take-profit, trailing stops, time-based exits.
        """
        if symbol not in self._entry_prices:
            return

        entry_price = self._entry_prices[symbol]

        # Hard stop: 2% adverse move (Phase 1 simple rule)
        stop_pct = 0.02
        adverse_move = abs(current_price - entry_price) / entry_price
        direction_adverse = current_price < entry_price  # long position going down

        if direction_adverse and adverse_move >= stop_pct:
            exit_direction = Direction.SHORT  # close long
            from bot.signals.base import AssetClass
            exit_signal = Signal(
                symbol=symbol,
                direction=exit_direction,
                confidence=1.0,
                price=current_price,
                timestamp=bar_ts,
                strategy="stop_loss_exit",
                asset_class=AssetClass.EQUITY,
                metadata={"entry_price": entry_price, "stop_pct": stop_pct},
            )
            qty = self._open_positions.get(symbol, 0)
            if qty > 0:
                self.broker.submit_order(signal=exit_signal, quantity=float(qty))
                self._close_position(symbol, entry_price, current_price, bar_ts, exit_reason="stop_loss")

    def _close_position(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        exit_ts: datetime,
        exit_reason: str = "stop_loss",
    ) -> None:
        """Record the closed trade in the tracker, persist to DB, and clean up state."""
        from datetime import timezone
        qty = self._open_positions.pop(symbol, 0)
        self._entry_prices.pop(symbol, None)
        entry_signal = self._entry_signals.pop(symbol, None)

        if qty <= 0:
            return

        closed = self.tracker.record_trade(
            symbol=symbol,
            side="long",
            entry_price=entry_price,
            exit_price=exit_price,
            qty=float(qty),
            entry_time=exit_ts,
            exit_time=exit_ts,
        )

        pnl = round((exit_price - entry_price) * qty, 2)
        log.info(
            "position_closed",
            symbol=symbol,
            entry_price=entry_price,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl=pnl,
        )

        if self.store and self._run_id and closed:
            self.store.save_trade(
                run_id=self._run_id,
                trade=closed,
                strategy=entry_signal.strategy if entry_signal else "",
                signal_confidence=entry_signal.confidence if entry_signal else 0.0,
                signal_direction=entry_signal.direction.value if entry_signal else "long",
                signal_strength=entry_signal.strength.value if entry_signal else "medium",
                signal_metadata=entry_signal.metadata if entry_signal else {},
                exit_reason=exit_reason,
                asset_class=entry_signal.asset_class.value if entry_signal else "equity",
            )

    # ── Data fetching ─────────────────────────────────────────────────────────

    def _fetch(
        self, symbol: str, start: datetime, end: datetime, interval: str
    ) -> pd.DataFrame | None:
        try:
            df = self.loader.fetch_bars(symbol, start=start, end=end, interval=interval)
            if df.empty:
                log.warning("no_data_for_symbol", symbol=symbol)
                return None
            return df
        except Exception:
            log.exception("data_fetch_failed", symbol=symbol)
            return None
