"""
Signal / Strategy base classes.

Every strategy in the system implements the Strategy ABC and returns Signal objects.
The backtest engine, paper loop, and live loop all work exclusively through this
interface — they never import a concrete strategy directly.

Adding a new strategy:
    1. Create bot/signals/your_strategy.py
    2. Subclass Strategy, implement generate_signals()
    3. Register it in bot/signals/__init__.py
    4. Pass it to BacktestEngine or the live loop — no other changes needed.

Design notes:
- Signal carries enough context for the risk manager to size the position
  and for the execution layer to submit the order, without knowing which
  strategy produced it.
- `metadata` is an open dict so future strategies (LLM sentiment, RL agents)
  can attach arbitrary context (e.g. confidence distribution, news sources)
  without changing the dataclass schema.
- `asset_class` enables the risk manager to apply different limits per class
  (e.g. no overnight for equities, higher volatility tolerance for crypto).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd


# ── Enums ────────────────────────────────────────────────────────────────────

class Direction(str, Enum):
    LONG  = "long"
    SHORT = "short"
    FLAT  = "flat"   # "exit / do nothing" signal


class AssetClass(str, Enum):
    EQUITY = "equity"
    CRYPTO = "crypto"
    OPTION = "option"


class SignalStrength(str, Enum):
    """Qualitative bucket — useful for position sizing tiers."""
    WEAK   = "weak"    # e.g. confidence 0.5–0.65
    MEDIUM = "medium"  # 0.65–0.80
    STRONG = "strong"  # 0.80+


# ── Signal dataclass ──────────────────────────────────────────────────────────

@dataclass
class Signal:
    """
    Output of a strategy's generate_signals() call for a single bar.

    Fields
    ------
    symbol      : ticker / trading pair (e.g. "AAPL", "BTC/USDT")
    direction   : LONG | SHORT | FLAT
    confidence  : 0.0–1.0 — used by risk manager for position sizing
    price       : reference price at signal generation time (usually bar close)
    timestamp   : bar timestamp that triggered this signal
    strategy    : name of the strategy that produced this signal
    asset_class : drives per-class risk rules
    strength    : qualitative tier derived from confidence
    stop_loss   : optional absolute price level for a hard stop (None = risk manager decides)
    take_profit : optional absolute price level for a take-profit (None = strategy decides later)
    metadata    : open dict for strategy-specific context (sentiment score, feature importances, etc.)
    """
    symbol:      str
    direction:   Direction
    confidence:  float          # 0.0 – 1.0
    price:       float
    timestamp:   datetime
    strategy:    str
    asset_class: AssetClass     = AssetClass.EQUITY
    strength:    SignalStrength = SignalStrength.MEDIUM
    stop_loss:   float | None  = None
    take_profit: float | None  = None
    metadata:    dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        # Auto-derive strength from confidence if not overridden
        if self.strength == SignalStrength.MEDIUM:
            if self.confidence < 0.65:
                self.strength = SignalStrength.WEAK
            elif self.confidence >= 0.80:
                self.strength = SignalStrength.STRONG

    def is_actionable(self) -> bool:
        """A FLAT signal means 'do nothing' — risk manager should skip it."""
        return self.direction != Direction.FLAT

    def to_dict(self) -> dict[str, Any]:
        """Serialise for logging and DB storage."""
        return {
            "symbol":      self.symbol,
            "direction":   self.direction.value,
            "confidence":  round(self.confidence, 4),
            "price":       self.price,
            "timestamp":   self.timestamp.isoformat(),
            "strategy":    self.strategy,
            "asset_class": self.asset_class.value,
            "strength":    self.strength.value,
            "stop_loss":   self.stop_loss,
            "take_profit": self.take_profit,
            "metadata":    self.metadata,
        }


# ── Strategy ABC ──────────────────────────────────────────────────────────────

class Strategy(ABC):
    """
    Abstract base for all trading strategies.

    Subclasses implement `generate_signals()` which receives a window of OHLCV
    bars and returns zero or more Signal objects.

    The interface is intentionally simple so it can be called identically by:
    - BacktestEngine (historical bar-by-bar replay)
    - Paper live loop (streaming bars)
    - Live execution loop (real-time bars)
    - Ensemble runner (multiple strategies in parallel, Phase 3+)

    Parameters passed at construction (not per-call) should be stored as
    instance attributes. Use `params` dict for anything that might be tuned
    or swept during optimisation.
    """

    #: Unique name — used in Signal.strategy, logs, and report grouping.
    #: Override in subclass: name = "momentum_rsi"
    name: str = "base"

    #: Asset class this strategy is designed for. Used by the risk manager
    #: to apply the right set of rules. Override in subclass.
    asset_class: AssetClass = AssetClass.EQUITY

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        """
        Args:
            params: Strategy-specific hyperparameters (e.g. {"rsi_period": 14}).
                    Stored as self.params for logging and future optimisation sweeps.
        """
        self.params: dict[str, Any] = params or {}

    @abstractmethod
    def generate_signals(self, bars: pd.DataFrame, symbol: str) -> list[Signal]:
        """
        Produce signals for `symbol` given a window of OHLCV bars.

        Args:
            bars   : DataFrame with columns [open, high, low, close, volume],
                     DatetimeIndex (UTC), sorted oldest-first.
                     The last row is the most recent (current) bar.
            symbol : The trading symbol being analysed.

        Returns:
            List of Signal objects (empty list = no signal this bar).
            Return a FLAT signal only if you want to explicitly close a position.
            Return an empty list to mean "no opinion".
        """

    def warm_up_bars(self) -> int:
        """
        Minimum number of historical bars needed before the strategy can produce
        a valid signal. The backtest engine will skip the first N bars.

        Override in subclass. Default 0 (no warm-up).
        """
        return 0

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, params={self.params})"
