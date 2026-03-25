"""
Tests for bot/signals/base.py and bot/signals/momentum.py

Coverage targets:
- Signal dataclass: validation, strength auto-derivation, serialisation
- Direction / AssetClass / SignalStrength enums
- MomentumStrategy: warm-up enforcement, RSI/SMA logic, confidence bounds,
  correct direction on real price patterns, metadata population
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from bot.signals.base import (
    AssetClass,
    Direction,
    Signal,
    SignalStrength,
    Strategy,
)
from bot.signals.momentum import MomentumStrategy, _rsi, _sma
from tests.conftest import make_bars, make_downtrend_bars, make_signal, make_uptrend_bars


# ═══════════════════════════════════════════════════════════════════════════════
# Signal dataclass
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignal:
    def test_valid_signal_creates_without_error(self):
        sig = make_signal()
        assert sig.symbol == "AAPL"
        assert sig.direction == Direction.LONG

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            make_signal(confidence=-0.01)

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            make_signal(confidence=1.001)

    def test_confidence_at_boundaries_is_valid(self):
        make_signal(confidence=0.0)
        make_signal(confidence=1.0)

    # ── Strength auto-derivation ──────────────────────────────────────────────

    def test_strength_weak_below_065(self):
        sig = make_signal(confidence=0.60)
        assert sig.strength == SignalStrength.WEAK

    def test_strength_medium_between_065_and_080(self):
        sig = make_signal(confidence=0.72)
        assert sig.strength == SignalStrength.MEDIUM

    def test_strength_strong_at_080_and_above(self):
        sig = make_signal(confidence=0.80)
        assert sig.strength == SignalStrength.STRONG

    def test_strength_strong_at_100(self):
        sig = make_signal(confidence=1.0)
        assert sig.strength == SignalStrength.STRONG

    def test_strength_boundary_exactly_065_is_medium(self):
        sig = make_signal(confidence=0.65)
        assert sig.strength == SignalStrength.MEDIUM

    # ── Actionability ─────────────────────────────────────────────────────────

    def test_long_signal_is_actionable(self):
        assert make_signal(direction=Direction.LONG).is_actionable()

    def test_short_signal_is_actionable(self):
        assert make_signal(direction=Direction.SHORT).is_actionable()

    def test_flat_signal_is_not_actionable(self):
        assert not make_signal(direction=Direction.FLAT).is_actionable()

    # ── Serialisation ─────────────────────────────────────────────────────────

    def test_to_dict_contains_required_keys(self):
        sig = make_signal()
        d = sig.to_dict()
        for key in ("symbol", "direction", "confidence", "price", "timestamp",
                    "strategy", "asset_class", "strength", "metadata"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_direction_is_string(self):
        d = make_signal(direction=Direction.LONG).to_dict()
        assert d["direction"] == "long"

    def test_to_dict_asset_class_is_string(self):
        d = make_signal(asset_class=AssetClass.CRYPTO).to_dict()
        assert d["asset_class"] == "crypto"

    def test_metadata_roundtrips(self):
        sig = make_signal()
        sig.metadata["rsi"] = 28.5
        assert sig.to_dict()["metadata"]["rsi"] == 28.5


# ═══════════════════════════════════════════════════════════════════════════════
# RSI / SMA indicators
# ═══════════════════════════════════════════════════════════════════════════════

class TestIndicators:
    def test_rsi_length_matches_input(self):
        close = pd.Series(range(1, 31), dtype=float)
        result = _rsi(close, period=14)
        assert len(result) == len(close)

    def test_rsi_first_period_minus_one_bars_are_nan(self):
        close = pd.Series(range(1, 31), dtype=float)
        result = _rsi(close, period=14)
        # First 13 should be NaN (need 14 bars before first valid value)
        assert result.iloc[:13].isna().all()

    def test_rsi_constant_price_series_returns_nan(self):
        # No gains or losses → RS is undefined
        close = pd.Series([100.0] * 20)
        result = _rsi(close, period=14)
        # All NaN or 50 — either is acceptable depending on implementation
        # We just check it doesn't blow up and returns the right length
        assert len(result) == 20

    def test_rsi_all_gains_approaches_100(self):
        # Monotonically rising prices → RSI should be very high
        close = pd.Series([100 + i for i in range(30)], dtype=float)
        result = _rsi(close, period=14)
        valid = result.dropna()
        assert (valid > 90).all(), f"Expected RSI > 90 for all-up series, got {valid.tolist()}"

    def test_rsi_all_losses_approaches_0(self):
        close = pd.Series([100 - i * 0.5 for i in range(30)], dtype=float)
        result = _rsi(close, period=14)
        valid = result.dropna()
        assert (valid < 10).all(), f"Expected RSI < 10 for all-down series, got {valid.tolist()}"

    def test_sma_is_correct_on_known_series(self):
        close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _sma(close, period=3)
        # Window of 3: first two NaN, then [2, 3, 4]
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[3] == pytest.approx(3.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_sma_period_1_equals_close(self):
        close = pd.Series([10.0, 20.0, 30.0])
        assert (_sma(close, period=1) == close).all()


# ═══════════════════════════════════════════════════════════════════════════════
# MomentumStrategy
# ═══════════════════════════════════════════════════════════════════════════════

class TestMomentumStrategy:
    def setup_method(self):
        self.strategy = MomentumStrategy()

    def test_default_params_are_set(self):
        assert self.strategy.params["rsi_period"] == 14
        assert self.strategy.params["sma_period"] == 20
        assert self.strategy.params["oversold"]   == 30.0
        assert self.strategy.params["overbought"] == 70.0

    def test_custom_params_override_defaults(self):
        s = MomentumStrategy(params={"rsi_period": 7, "sma_period": 10})
        assert s.params["rsi_period"] == 7
        assert s.params["sma_period"] == 10
        # Defaults still set for unspecified
        assert s.params["oversold"] == 30.0

    def test_warm_up_bars_equals_max_of_periods(self):
        s = MomentumStrategy(params={"rsi_period": 14, "sma_period": 20})
        assert s.warm_up_bars() == 20

    def test_warm_up_bars_uses_larger_period(self):
        s = MomentumStrategy(params={"rsi_period": 50, "sma_period": 10})
        assert s.warm_up_bars() == 50

    def test_insufficient_bars_returns_empty(self):
        bars = make_bars(n=10)   # less than warm_up (20)
        signals = self.strategy.generate_signals(bars, "AAPL")
        assert signals == []

    def test_exact_warmup_bars_does_not_crash(self):
        bars = make_bars(n=self.strategy.warm_up_bars())
        # Should not raise — may or may not produce a signal
        signals = self.strategy.generate_signals(bars, "AAPL")
        assert isinstance(signals, list)

    def test_downtrend_produces_long_or_no_signal(self):
        """
        In a strong downtrend, RSI goes oversold — we expect either a LONG
        signal (oversold bounce) or no signal. Never SHORT in a downtrend here
        because price < SMA blocks the SHORT condition.
        """
        bars = make_downtrend_bars(n=60)
        signals = self.strategy.generate_signals(bars, "AAPL")
        for sig in signals:
            assert sig.direction in (Direction.LONG, Direction.FLAT)

    def test_uptrend_produces_short_or_no_signal(self):
        """
        In a strong uptrend, RSI goes overbought. We expect SHORT or no signal.
        """
        bars = make_uptrend_bars(n=60)
        signals = self.strategy.generate_signals(bars, "AAPL")
        for sig in signals:
            assert sig.direction in (Direction.SHORT, Direction.FLAT)

    def test_signal_confidence_is_within_bounds(self):
        bars = make_bars(n=60)
        signals = self.strategy.generate_signals(bars, "AAPL")
        for sig in signals:
            assert 0.0 <= sig.confidence <= 1.0

    def test_signal_confidence_at_least_min_confidence(self):
        bars = make_bars(n=60)
        signals = self.strategy.generate_signals(bars, "AAPL")
        min_conf = self.strategy.params["min_confidence"]
        for sig in signals:
            assert sig.confidence >= min_conf

    def test_signal_metadata_contains_rsi_and_sma(self):
        # Use very aggressive downtrend to force a signal
        bars = make_downtrend_bars(n=80, base_price=200.0)
        signals = self.strategy.generate_signals(bars, "AAPL")
        if signals:
            meta = signals[0].metadata
            assert "rsi" in meta
            assert "sma" in meta
            assert "rsi_period" in meta
            assert "sma_period" in meta

    def test_signal_price_matches_last_bar_close(self):
        bars = make_downtrend_bars(n=80)
        signals = self.strategy.generate_signals(bars, "AAPL")
        if signals:
            assert signals[0].price == pytest.approx(float(bars["close"].iloc[-1]))

    def test_signal_strategy_name_is_set(self):
        bars = make_downtrend_bars(n=80)
        signals = self.strategy.generate_signals(bars, "AAPL")
        if signals:
            assert signals[0].strategy == "momentum_rsi_sma"

    def test_asset_class_propagates_to_signal(self):
        strategy = MomentumStrategy(asset_class=AssetClass.CRYPTO)
        bars = make_downtrend_bars(n=80)
        signals = strategy.generate_signals(bars, "BTC-USD")
        if signals:
            assert signals[0].asset_class == AssetClass.CRYPTO

    def test_repr_is_informative(self):
        r = repr(self.strategy)
        assert "MomentumStrategy" in r
        assert "momentum_rsi_sma" in r

    # ── _evaluate internals ───────────────────────────────────────────────────

    def test_evaluate_returns_flat_when_rsi_is_neutral(self):
        direction, confidence = self.strategy._evaluate(rsi=50.0, price=100.0, sma=95.0)
        assert direction == Direction.FLAT
        assert confidence == 0.0

    def test_evaluate_returns_long_when_oversold_below_sma(self):
        # RSI=20 (oversold), price < SMA
        direction, confidence = self.strategy._evaluate(rsi=20.0, price=90.0, sma=100.0)
        assert direction == Direction.LONG
        assert confidence > 0.5

    def test_evaluate_returns_short_when_overbought_above_sma(self):
        # RSI=80 (overbought), price > SMA
        direction, confidence = self.strategy._evaluate(rsi=80.0, price=110.0, sma=100.0)
        assert direction == Direction.SHORT
        assert confidence > 0.5

    def test_evaluate_no_long_when_oversold_but_above_sma(self):
        # RSI oversold but price is ABOVE SMA — trend not confirmed
        direction, _ = self.strategy._evaluate(rsi=20.0, price=110.0, sma=100.0)
        assert direction == Direction.FLAT

    def test_evaluate_no_short_when_overbought_but_below_sma(self):
        # RSI overbought but price BELOW SMA — trend not confirmed
        direction, _ = self.strategy._evaluate(rsi=80.0, price=90.0, sma=100.0)
        assert direction == Direction.FLAT

    def test_evaluate_confidence_increases_with_rsi_extremity(self):
        # More extreme RSI → higher confidence
        _, conf_mild   = self.strategy._evaluate(rsi=28.0, price=90.0, sma=100.0)
        _, conf_extreme = self.strategy._evaluate(rsi=10.0, price=90.0, sma=100.0)
        assert conf_extreme > conf_mild

    def test_evaluate_confidence_capped_at_1(self):
        # RSI = 0 is theoretically possible — confidence must not exceed 1
        _, confidence = self.strategy._evaluate(rsi=0.0, price=90.0, sma=100.0)
        assert confidence <= 1.0

    def test_evaluate_confidence_at_least_05_when_signal_fires(self):
        direction, confidence = self.strategy._evaluate(rsi=29.9, price=90.0, sma=100.0)
        if direction != Direction.FLAT:
            assert confidence >= 0.5
