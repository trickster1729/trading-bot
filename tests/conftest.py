"""
Shared pytest fixtures used across all test modules.

Fixtures here are available to every test file without importing.
Add new shared fixtures here; keep module-specific fixtures in their own file.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from bot.monitoring.metrics import PerformanceTracker
from bot.risk.limits import RiskLimits
from bot.risk.manager import RiskManager
from bot.signals.base import AssetClass, Direction, Signal


# ── Factories ─────────────────────────────────────────────────────────────────

def make_signal(
    symbol: str = "AAPL",
    direction: Direction = Direction.LONG,
    confidence: float = 0.75,
    price: float = 150.0,
    asset_class: AssetClass = AssetClass.EQUITY,
    strategy: str = "test_strategy",
) -> Signal:
    return Signal(
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        price=price,
        timestamp=datetime(2024, 1, 15, 16, 0, tzinfo=timezone.utc),
        strategy=strategy,
        asset_class=asset_class,
    )


def make_bars(
    n: int = 60,
    base_price: float = 100.0,
    trend: float = 0.001,       # daily drift
    volatility: float = 0.01,   # daily std dev
    seed: int = 42,
) -> pd.DataFrame:
    """
    Synthetic OHLCV bars with a configurable trend and noise.
    Deterministic via seed so tests are reproducible.
    """
    import numpy as np
    rng = np.random.default_rng(seed)

    closes = [base_price]
    for _ in range(n - 1):
        ret = trend + rng.normal(0, volatility)
        closes.append(closes[-1] * (1 + ret))

    closes = pd.Series(closes)
    highs  = closes * (1 + rng.uniform(0, 0.005, n))
    lows   = closes * (1 - rng.uniform(0, 0.005, n))
    opens  = closes.shift(1).fillna(closes)
    volume = rng.uniform(1_000_000, 5_000_000, n)

    idx = pd.date_range("2023-01-01", periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume},
        index=idx,
    )


def make_downtrend_bars(n: int = 60, base_price: float = 100.0) -> pd.DataFrame:
    """Bars with a clear downtrend — RSI will be oversold."""
    return make_bars(n=n, base_price=base_price, trend=-0.005, volatility=0.005, seed=99)


def make_uptrend_bars(n: int = 60, base_price: float = 100.0) -> pd.DataFrame:
    """Bars with a clear uptrend — RSI will be overbought."""
    return make_bars(n=n, base_price=base_price, trend=0.005, volatility=0.005, seed=77)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tracker():
    return PerformanceTracker(initial_capital=10_000.0)


@pytest.fixture
def default_limits():
    return RiskLimits()


@pytest.fixture
def risk_manager(tracker, default_limits):
    return RiskManager(limits=default_limits, tracker=tracker)


@pytest.fixture
def long_signal():
    return make_signal(direction=Direction.LONG, confidence=0.75)


@pytest.fixture
def short_signal():
    return make_signal(direction=Direction.SHORT, confidence=0.75)
