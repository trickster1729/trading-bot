"""
Broker abstraction layer.

Every broker (paper, Alpaca, Binance, IBKR) implements the Broker ABC.
The backtest engine and live loop only ever call submit_order() and
cancel_order() — they never import a concrete broker.

Swapping paper → live is a one-line config change:
    broker = PaperBroker(tracker)      # Phase 1
    broker = AlpacaBroker(api_key=..)  # Phase 2+

OrderResult carries enough information for:
- The performance tracker to record the trade
- The logger to emit a structured log entry
- The DB layer to persist the record
- Future reconciliation against broker statements

Phase 3+ extension points
--------------------------
- Add get_open_orders() for the kill switch to cancel everything
- Add get_positions() for the live loop to sync state with the broker
- Add async variants (submit_order_async) for low-latency paths
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from bot.signals.base import Direction, Signal


# ── Order status ──────────────────────────────────────────────────────────────

class OrderStatus(str, Enum):
    FILLED    = "filled"
    REJECTED  = "rejected"
    CANCELLED = "cancelled"
    PENDING   = "pending"    # submitted but not yet confirmed (async brokers)


class OrderSide(str, Enum):
    BUY  = "buy"
    SELL = "sell"

    @classmethod
    def from_direction(cls, direction: Direction) -> "OrderSide":
        return cls.BUY if direction == Direction.LONG else cls.SELL


# ── Order result ──────────────────────────────────────────────────────────────

@dataclass
class OrderResult:
    """
    The broker's response to a submitted order.

    Fields
    ------
    order_id     : broker-assigned ID (UUID for paper broker, broker string for live)
    status       : FILLED | REJECTED | CANCELLED | PENDING
    symbol       : trading symbol
    side         : BUY | SELL
    requested_qty: units requested
    filled_qty   : units actually filled (may differ due to partial fills)
    fill_price   : actual execution price (may differ from signal price due to slippage)
    commission   : total commission charged
    timestamp    : time of fill (or rejection)
    signal       : the Signal that generated this order (for audit trail)
    error_msg    : populated on REJECTED/CANCELLED
    metadata     : broker-specific extras (e.g. Alpaca order object fields)
    """
    order_id:      str
    status:        OrderStatus
    symbol:        str
    side:          OrderSide
    requested_qty: float
    filled_qty:    float
    fill_price:    float
    commission:    float
    timestamp:     datetime
    signal:        Signal
    error_msg:     str | None           = None
    metadata:      dict[str, Any]       = field(default_factory=dict)

    @property
    def slippage(self) -> float:
        """Absolute price slippage vs. signal price."""
        return abs(self.fill_price - self.signal.price)

    @property
    def slippage_bps(self) -> float:
        """Slippage in basis points."""
        if self.signal.price == 0:
            return 0.0
        return (self.slippage / self.signal.price) * 10_000

    @property
    def filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id":      self.order_id,
            "status":        self.status.value,
            "symbol":        self.symbol,
            "side":          self.side.value,
            "requested_qty": self.requested_qty,
            "filled_qty":    self.filled_qty,
            "fill_price":    self.fill_price,
            "commission":    self.commission,
            "slippage_bps":  round(self.slippage_bps, 2),
            "timestamp":     self.timestamp.isoformat(),
            "error_msg":     self.error_msg,
        }


# ── Broker ABC ────────────────────────────────────────────────────────────────

class Broker(ABC):
    """
    Abstract base for all brokers (paper, Alpaca, Binance, IBKR).

    Subclasses implement submit_order() and cancel_order().
    The live loop also calls get_open_orders() and get_positions() (Phase 2+).
    """

    @abstractmethod
    def submit_order(
        self,
        signal: Signal,
        quantity: float,
        *,
        order_type: str = "market",
        limit_price: float | None = None,
    ) -> OrderResult:
        """
        Submit an order derived from `signal`.

        Args:
            signal      : the Signal that triggered this order
            quantity    : position size from RiskManager (units/shares/contracts)
            order_type  : "market" | "limit" | "stop" (broker-dependent support)
            limit_price : required if order_type == "limit"

        Returns:
            OrderResult — always returns one, never raises on broker rejection.
            Check result.status to determine outcome.
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order by ID.

        Returns:
            True if cancelled successfully, False if already filled/not found.
        """

    def cancel_all_orders(self) -> list[str]:
        """
        Cancel all open orders. Used by the kill switch.
        Default: no-op (override in live brokers).
        Returns list of cancelled order IDs.
        """
        return []

    def get_open_positions(self) -> dict[str, float]:
        """
        Return current open positions: symbol → quantity.
        Default: empty (paper broker tracks this internally; live brokers query API).
        """
        return {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"
