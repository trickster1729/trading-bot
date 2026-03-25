"""
Paper broker — simulates order execution without any real money.

Used in Phase 1 (shadow/backtest) and Phase 2 (paper live).
Designed to be as realistic as possible so paper results translate
meaningfully to live results.

Simulation features
-------------------
- Market orders: filled at close price + configurable slippage model
- Limit orders: filled only if price crosses the limit (checked on next bar)
- Commission model: flat per-trade or percentage (matches Alpaca/Binance defaults)
- Partial fills: not simulated in Phase 1 (all-or-nothing), extensible for Phase 3
- Position tracking: maintains open positions dict for kill switch + risk manager

Slippage model (Phase 1)
------------------------
Market orders are filled at:
    fill_price = close_price * (1 + slippage_bps/10_000)  # for buys
    fill_price = close_price * (1 - slippage_bps/10_000)  # for sells

Default 5 bps — conservative for liquid US equities. Adjust higher for
crypto or illiquid names. Phase 3 can replace with a volume-weighted model.

Reference: AutoTrader virtual broker implementation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from bot.execution.base import Broker, OrderResult, OrderSide, OrderStatus
from bot.monitoring.logger import get_logger
from bot.signals.base import Signal

log = get_logger(__name__)


class PaperBroker(Broker):
    """
    Simulated broker for backtesting and paper trading.

    Args:
        slippage_bps : slippage per market order in basis points (default 5)
        commission   : flat commission per trade in dollars (default 0.0 — Alpaca-style)
        commission_pct: percentage commission as fraction (default 0.0 — use one or the other)
    """

    def __init__(
        self,
        slippage_bps: float = 10.0,   # conservative: see ADR-009
        commission: float = 0.0,
        commission_pct: float = 0.0,
    ) -> None:
        self.slippage_bps   = slippage_bps
        self.commission     = commission
        self.commission_pct = commission_pct

        # symbol → quantity (positive = long, negative = short)
        self._open_positions: dict[str, float] = {}
        # order_id → OrderResult (for cancel support and audit)
        self._order_log: dict[str, OrderResult] = {}

    # ── Broker interface ──────────────────────────────────────────────────────

    def submit_order(
        self,
        signal: Signal,
        quantity: float,
        *,
        order_type: str = "market",
        limit_price: float | None = None,
    ) -> OrderResult:
        order_id = str(uuid.uuid4())
        side = OrderSide.from_direction(signal.direction)
        now = datetime.now(tz=timezone.utc)

        if order_type == "market":
            result = self._fill_market(
                order_id=order_id,
                signal=signal,
                side=side,
                quantity=quantity,
                timestamp=now,
            )
        elif order_type == "limit":
            if limit_price is None:
                raise ValueError("limit_price required for limit orders")
            result = self._fill_limit(
                order_id=order_id,
                signal=signal,
                side=side,
                quantity=quantity,
                limit_price=limit_price,
                timestamp=now,
            )
        else:
            result = self._reject(
                order_id=order_id,
                signal=signal,
                side=side,
                quantity=quantity,
                timestamp=now,
                reason=f"unsupported_order_type_{order_type}",
            )

        self._order_log[order_id] = result
        self._update_positions(result)
        self._log_result(result)
        return result

    def cancel_order(self, order_id: str) -> bool:
        result = self._order_log.get(order_id)
        if result is None or result.status != OrderStatus.PENDING:
            return False
        # Mutate status — in practice pending orders aren't common in paper mode
        object.__setattr__(result, "status", OrderStatus.CANCELLED)
        log.info("order_cancelled", order_id=order_id, symbol=result.symbol)
        return True

    def cancel_all_orders(self) -> list[str]:
        cancelled = []
        for order_id, result in self._order_log.items():
            if result.status == OrderStatus.PENDING:
                if self.cancel_order(order_id):
                    cancelled.append(order_id)
        return cancelled

    def get_open_positions(self) -> dict[str, float]:
        return dict(self._open_positions)

    # ── Fill simulation ───────────────────────────────────────────────────────

    def _fill_market(
        self,
        order_id: str,
        signal: Signal,
        side: OrderSide,
        quantity: float,
        timestamp: datetime,
    ) -> OrderResult:
        slippage_factor = self.slippage_bps / 10_000
        if side == OrderSide.BUY:
            fill_price = signal.price * (1 + slippage_factor)
        else:
            fill_price = signal.price * (1 - slippage_factor)

        commission = self._calc_commission(fill_price * quantity)

        return OrderResult(
            order_id=order_id,
            status=OrderStatus.FILLED,
            symbol=signal.symbol,
            side=side,
            requested_qty=quantity,
            filled_qty=quantity,
            fill_price=round(fill_price, 6),
            commission=round(commission, 4),
            timestamp=timestamp,
            signal=signal,
        )

    def _fill_limit(
        self,
        order_id: str,
        signal: Signal,
        side: OrderSide,
        quantity: float,
        limit_price: float,
        timestamp: datetime,
    ) -> OrderResult:
        # In paper mode, a limit order fills immediately if the current price
        # is at or better than the limit. Otherwise it's left PENDING.
        # The live loop is responsible for checking PENDING orders on subsequent bars.
        can_fill = (
            (side == OrderSide.BUY  and signal.price <= limit_price) or
            (side == OrderSide.SELL and signal.price >= limit_price)
        )
        if can_fill:
            commission = self._calc_commission(limit_price * quantity)
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.FILLED,
                symbol=signal.symbol,
                side=side,
                requested_qty=quantity,
                filled_qty=quantity,
                fill_price=limit_price,
                commission=round(commission, 4),
                timestamp=timestamp,
                signal=signal,
            )
        else:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.PENDING,
                symbol=signal.symbol,
                side=side,
                requested_qty=quantity,
                filled_qty=0.0,
                fill_price=0.0,
                commission=0.0,
                timestamp=timestamp,
                signal=signal,
                metadata={"limit_price": limit_price},
            )

    def _reject(
        self,
        order_id: str,
        signal: Signal,
        side: OrderSide,
        quantity: float,
        timestamp: datetime,
        reason: str,
    ) -> OrderResult:
        return OrderResult(
            order_id=order_id,
            status=OrderStatus.REJECTED,
            symbol=signal.symbol,
            side=side,
            requested_qty=quantity,
            filled_qty=0.0,
            fill_price=0.0,
            commission=0.0,
            timestamp=timestamp,
            signal=signal,
            error_msg=reason,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _calc_commission(self, trade_value: float) -> float:
        return self.commission + (trade_value * self.commission_pct)

    def _update_positions(self, result: OrderResult) -> None:
        if not result.filled:
            return
        qty = result.filled_qty
        if result.side == OrderSide.SELL:
            qty = -qty
        current = self._open_positions.get(result.symbol, 0.0)
        new_qty = current + qty
        if abs(new_qty) < 1e-9:
            self._open_positions.pop(result.symbol, None)
        else:
            self._open_positions[result.symbol] = new_qty

    def _log_result(self, result: OrderResult) -> None:
        if result.filled:
            log.info(
                "order_filled",
                broker="paper",
                **result.to_dict(),
            )
        elif result.status == OrderStatus.REJECTED:
            log.error(
                "order_rejected",
                broker="paper",
                **result.to_dict(),
            )
        elif result.status == OrderStatus.PENDING:
            log.debug(
                "order_pending",
                broker="paper",
                **result.to_dict(),
            )
