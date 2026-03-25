"""
Momentum strategy — Phase 1 baseline.

Signal logic
------------
Uses two indicators in combination:

1. RSI (Relative Strength Index, default 14-period)
   - RSI < oversold_threshold  → potential LONG (oversold bounce)
   - RSI > overbought_threshold → potential SHORT (overbought reversal)

2. Price vs. moving average (default 20-period SMA)
   - Price above SMA → confirms uptrend (long-friendly)
   - Price below SMA → confirms downtrend (short-friendly)

A signal is only emitted when BOTH conditions agree.
Confidence is derived from how extreme the RSI reading is.

This is intentionally simple — it establishes the pipeline end-to-end.
The same Strategy interface will be used by MeanReversion, Sentiment,
ML, and RL strategies without touching the backtest engine or risk layer.

Reference: backtesting.py strategy examples, Jesse framework patterns.

Parameters (all overridable via `params` dict)
----------------------------------------------
rsi_period         : int   = 14
sma_period         : int   = 20
oversold           : float = 30.0
overbought         : float = 70.0
min_confidence     : float = 0.5    — signals below this are suppressed
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from bot.monitoring.logger import get_logger
from bot.signals.base import AssetClass, Direction, Signal, SignalStrength, Strategy

log = get_logger(__name__)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """
    Wilder's RSI.  Returns a Series aligned with `close`, NaN for the
    first `period` rows.  Pure-pandas, no TA-Lib dependency.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    # Exponential moving average with alpha = 1/period (Wilder's smoothing)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(window=period, min_periods=period).mean()


class MomentumStrategy(Strategy):
    """
    RSI + SMA momentum strategy.

    Works for any asset class — set `asset_class` at construction when
    using for crypto or options so the risk manager applies the right rules.
    """

    name = "momentum_rsi_sma"

    def __init__(
        self,
        params: dict[str, Any] | None = None,
        asset_class: AssetClass = AssetClass.EQUITY,
    ) -> None:
        defaults: dict[str, Any] = {
            "rsi_period":     14,
            "sma_period":     20,
            "oversold":       30.0,
            "overbought":     70.0,
            "min_confidence": 0.50,
        }
        merged = {**defaults, **(params or {})}
        super().__init__(params=merged)
        self.asset_class = asset_class

    # ── Warm-up ───────────────────────────────────────────────────────────────

    def warm_up_bars(self) -> int:
        # Need enough bars for both indicators to stabilise
        return max(self.params["rsi_period"], self.params["sma_period"])

    # ── Core logic ────────────────────────────────────────────────────────────

    def generate_signals(self, bars: pd.DataFrame, symbol: str) -> list[Signal]:
        """
        Evaluate the most recent bar and return 0 or 1 Signal.

        We only act on the *last* bar (the current bar in the replay loop).
        All prior bars are context for indicator calculation only.
        """
        if len(bars) < self.warm_up_bars():
            log.debug(
                "insufficient_bars_for_warmup",
                strategy=self.name,
                symbol=symbol,
                have=len(bars),
                need=self.warm_up_bars(),
            )
            return []

        close = bars["close"]
        rsi_series = _rsi(close, self.params["rsi_period"])
        sma_series = _sma(close, self.params["sma_period"])

        current_rsi  = rsi_series.iloc[-1]
        current_sma  = sma_series.iloc[-1]
        current_close = close.iloc[-1]
        current_ts    = bars.index[-1].to_pydatetime()

        if pd.isna(current_rsi) or pd.isna(current_sma):
            return []

        direction, confidence = self._evaluate(
            rsi=current_rsi,
            price=current_close,
            sma=current_sma,
        )

        if direction == Direction.FLAT:
            return []

        if confidence < self.params["min_confidence"]:
            log.debug(
                "signal_below_min_confidence",
                strategy=self.name,
                symbol=symbol,
                direction=direction.value,
                confidence=round(confidence, 3),
                min_confidence=self.params["min_confidence"],
            )
            return []

        signal = Signal(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            price=current_close,
            timestamp=current_ts,
            strategy=self.name,
            asset_class=self.asset_class,
            metadata={
                "rsi":         round(float(current_rsi), 2),
                "sma":         round(float(current_sma), 2),
                "rsi_period":  self.params["rsi_period"],
                "sma_period":  self.params["sma_period"],
            },
        )

        log.info(
            "signal_generated",
            strategy=self.name,
            symbol=symbol,
            direction=direction.value,
            confidence=round(confidence, 3),
            strength=signal.strength.value,
            rsi=round(float(current_rsi), 2),
            price=current_close,
        )

        return [signal]

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _evaluate(
        self, rsi: float, price: float, sma: float
    ) -> tuple[Direction, float]:
        """
        Return (direction, confidence) for the current bar.

        Confidence formula
        ------------------
        For a LONG signal (RSI oversold + price below SMA):
            confidence = (oversold_threshold - rsi) / oversold_threshold
            clamped to [0.5, 1.0]

        For a SHORT signal (RSI overbought + price above SMA):
            confidence = (rsi - overbought_threshold) / (100 - overbought_threshold)
            clamped to [0.5, 1.0]

        The further RSI is from the threshold the more confident we are.
        """
        oversold   = self.params["oversold"]
        overbought = self.params["overbought"]

        is_oversold   = rsi < oversold
        is_overbought = rsi > overbought
        price_below_sma = price < sma
        price_above_sma = price > sma

        # LONG: RSI oversold AND price confirmed below SMA (mean-revert upward)
        if is_oversold and price_below_sma:
            raw = (oversold - rsi) / oversold
            confidence = float(np.clip(0.5 + raw * 0.5, 0.5, 1.0))
            return Direction.LONG, confidence

        # SHORT: RSI overbought AND price confirmed above SMA
        if is_overbought and price_above_sma:
            raw = (rsi - overbought) / (100 - overbought)
            confidence = float(np.clip(0.5 + raw * 0.5, 0.5, 1.0))
            return Direction.SHORT, confidence

        return Direction.FLAT, 0.0
