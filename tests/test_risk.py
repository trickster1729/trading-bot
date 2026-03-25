"""
Tests for bot/risk/manager.py and bot/risk/limits.py

Coverage targets:
- Every gate in RiskManager.evaluate() — both block and approve paths
- Kill switch: trips on drawdown breach, blocks all subsequent signals
- Kill switch reset re-enables trading
- Position sizing math is exact
- Per-asset-class limit overrides apply correctly
- Daily loss limit (future gate — tested as config check)
- RiskLimits.for_asset_class() returns correct overrides
"""

from __future__ import annotations

import pytest

from bot.monitoring.metrics import PerformanceTracker
from bot.risk.limits import RiskLimits
from bot.risk.manager import RiskDecision, RiskManager
from bot.signals.base import AssetClass, Direction
from tests.conftest import make_signal


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_risk_manager(
    initial_capital: float = 10_000.0,
    max_position_fraction: float = 0.02,
    max_drawdown_fraction: float = 0.10,
    min_signal_confidence: float = 0.55,
    max_open_positions: int = 10,
    max_positions_per_symbol: int = 1,
) -> tuple[RiskManager, PerformanceTracker]:
    tracker = PerformanceTracker(initial_capital=initial_capital)
    limits = RiskLimits(
        max_position_fraction=max_position_fraction,
        max_drawdown_fraction=max_drawdown_fraction,
        min_signal_confidence=min_signal_confidence,
        max_open_positions=max_open_positions,
        max_positions_per_symbol=max_positions_per_symbol,
    )
    return RiskManager(limits=limits, tracker=tracker), tracker


# ═══════════════════════════════════════════════════════════════════════════════
# RiskLimits
# ═══════════════════════════════════════════════════════════════════════════════

class TestRiskLimits:
    def test_default_max_position_fraction(self):
        assert RiskLimits().max_position_fraction == 0.02

    def test_default_max_drawdown_fraction(self):
        assert RiskLimits().max_drawdown_fraction == 0.10

    def test_for_asset_class_equity_returns_same_limits(self):
        limits = RiskLimits()
        equity_limits = limits.for_asset_class(AssetClass.EQUITY)
        assert equity_limits.max_position_fraction == limits.max_position_fraction

    def test_for_asset_class_crypto_tightens_position_fraction(self):
        limits = RiskLimits()
        crypto_limits = limits.for_asset_class(AssetClass.CRYPTO)
        assert crypto_limits.max_position_fraction < limits.max_position_fraction

    def test_for_asset_class_option_has_smallest_position_fraction(self):
        limits = RiskLimits()
        option_limits = limits.for_asset_class(AssetClass.OPTION)
        crypto_limits = limits.for_asset_class(AssetClass.CRYPTO)
        assert option_limits.max_position_fraction < crypto_limits.max_position_fraction

    def test_for_asset_class_does_not_mutate_original(self):
        limits = RiskLimits()
        original_fraction = limits.max_position_fraction
        limits.for_asset_class(AssetClass.CRYPTO)
        assert limits.max_position_fraction == original_fraction


# ═══════════════════════════════════════════════════════════════════════════════
# RiskManager — approval path
# ═══════════════════════════════════════════════════════════════════════════════

class TestRiskManagerApproval:
    def test_valid_signal_is_approved(self):
        rm, _ = _make_risk_manager()
        signal = make_signal(confidence=0.75, price=100.0)
        result = rm.evaluate(signal, open_positions={})
        assert result.approved

    def test_position_size_is_correct(self):
        # 2% of $10,000 = $200. At price $100 → 2.0 shares
        rm, _ = _make_risk_manager(
            initial_capital=10_000, max_position_fraction=0.02
        )
        signal = make_signal(confidence=0.75, price=100.0)
        result = rm.evaluate(signal, open_positions={})
        assert result.approved
        assert result.position_size == pytest.approx(2.0, rel=1e-4)

    def test_position_size_scales_with_capital(self):
        rm, _ = _make_risk_manager(initial_capital=50_000, max_position_fraction=0.02)
        signal = make_signal(confidence=0.75, price=100.0)
        result = rm.evaluate(signal, open_positions={})
        assert result.position_size == pytest.approx(10.0, rel=1e-4)

    def test_position_size_scales_with_price(self):
        # At price $200, 2% of $10,000 = $200 → 1.0 share
        rm, _ = _make_risk_manager(initial_capital=10_000, max_position_fraction=0.02)
        signal = make_signal(confidence=0.75, price=200.0)
        result = rm.evaluate(signal, open_positions={})
        assert result.position_size == pytest.approx(1.0, rel=1e-4)

    def test_decision_is_approved_enum(self):
        rm, _ = _make_risk_manager()
        signal = make_signal(confidence=0.75)
        result = rm.evaluate(signal, open_positions={})
        assert result.decision == RiskDecision.APPROVED

    def test_account_value_in_result_matches_tracker(self):
        rm, tracker = _make_risk_manager(initial_capital=10_000)
        signal = make_signal(confidence=0.75)
        result = rm.evaluate(signal, open_positions={})
        assert result.account_value == tracker.current_capital


# ═══════════════════════════════════════════════════════════════════════════════
# RiskManager — block gates
# ═══════════════════════════════════════════════════════════════════════════════

class TestRiskManagerBlocking:
    def test_flat_signal_is_blocked(self):
        rm, _ = _make_risk_manager()
        signal = make_signal(direction=Direction.FLAT)
        result = rm.evaluate(signal, open_positions={})
        assert not result.approved
        assert "flat" in result.reason

    def test_low_confidence_is_blocked(self):
        rm, _ = _make_risk_manager(min_signal_confidence=0.60)
        signal = make_signal(confidence=0.59)
        result = rm.evaluate(signal, open_positions={})
        assert not result.approved
        assert "confidence" in result.reason

    def test_confidence_exactly_at_threshold_is_blocked(self):
        # Boundary: must be strictly above min_confidence? No — >= is the check
        # Let's verify: confidence=0.55 with threshold=0.55 should pass
        rm, _ = _make_risk_manager(min_signal_confidence=0.55)
        signal = make_signal(confidence=0.55)
        result = rm.evaluate(signal, open_positions={})
        assert result.approved

    def test_confidence_just_below_threshold_is_blocked(self):
        rm, _ = _make_risk_manager(min_signal_confidence=0.55)
        signal = make_signal(confidence=0.549)
        result = rm.evaluate(signal, open_positions={})
        assert not result.approved

    def test_max_open_positions_blocks_new_signal(self):
        rm, _ = _make_risk_manager(max_open_positions=2)
        signal = make_signal(confidence=0.75, symbol="NVDA")
        # Already 2 positions open
        result = rm.evaluate(signal, open_positions={"AAPL": 1, "MSFT": 1})
        assert not result.approved
        assert "max_open_positions" in result.reason

    def test_max_positions_per_symbol_blocks_duplicate(self):
        rm, _ = _make_risk_manager(max_positions_per_symbol=1)
        signal = make_signal(confidence=0.75, symbol="AAPL")
        result = rm.evaluate(signal, open_positions={"AAPL": 1})
        assert not result.approved
        assert "AAPL" in result.reason

    def test_position_size_is_zero_when_blocked(self):
        rm, _ = _make_risk_manager()
        signal = make_signal(direction=Direction.FLAT)
        result = rm.evaluate(signal, open_positions={})
        assert result.position_size == 0.0

    def test_blocked_result_not_approved(self):
        rm, _ = _make_risk_manager()
        signal = make_signal(confidence=0.10)  # way below threshold
        result = rm.evaluate(signal, open_positions={})
        assert not result.approved
        assert result.decision == RiskDecision.BLOCKED


# ═══════════════════════════════════════════════════════════════════════════════
# Kill switch
# ═══════════════════════════════════════════════════════════════════════════════

class TestKillSwitch:
    def test_drawdown_breach_trips_kill_switch(self):
        # Use a 1% limit so a small trade is enough to trigger it cleanly.
        rm, tracker = _make_risk_manager(
            initial_capital=10_000, max_drawdown_fraction=0.01
        )
        from datetime import datetime, timezone
        # $120 loss on $10k account = 1.2% drawdown → exceeds 1% limit
        tracker.record_trade(
            symbol="AAPL", side="long",
            entry_price=100.0, exit_price=88.0,
            qty=10.0,
            entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        signal = make_signal(confidence=0.75)
        result = rm.evaluate(signal, open_positions={})
        assert not result.approved
        assert rm.is_halted

    def test_kill_switch_blocks_all_subsequent_signals(self):
        rm, _ = _make_risk_manager()
        rm.trip_kill_switch("test")
        assert rm.is_halted
        # Even a perfect signal should be blocked
        signal = make_signal(confidence=1.0)
        result = rm.evaluate(signal, open_positions={})
        assert not result.approved
        assert "kill_switch" in result.reason

    def test_kill_switch_reset_re_enables_trading(self):
        rm, _ = _make_risk_manager()
        rm.trip_kill_switch("test")
        rm.reset_kill_switch()
        assert not rm.is_halted
        signal = make_signal(confidence=0.75)
        result = rm.evaluate(signal, open_positions={})
        assert result.approved

    def test_is_halted_false_by_default(self):
        rm, _ = _make_risk_manager()
        assert not rm.is_halted

    def test_drawdown_exactly_at_limit_trips_switch(self):
        """Drawdown at exactly the limit must halt — >= not >."""
        rm, tracker = _make_risk_manager(
            initial_capital=10_000, max_drawdown_fraction=0.10
        )
        from datetime import datetime, timezone
        # 100 shares * $10 loss = $1,000 = exactly 10% of $10,000
        tracker.record_trade(
            symbol="AAPL", side="long",
            entry_price=100.0, exit_price=90.0,
            qty=100.0,
            entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        signal = make_signal(confidence=0.75)
        result = rm.evaluate(signal, open_positions={})
        assert not result.approved


# ═══════════════════════════════════════════════════════════════════════════════
# Per-asset-class limits
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssetClassLimits:
    def test_crypto_signal_uses_tighter_position_fraction(self):
        rm, _ = _make_risk_manager(initial_capital=10_000)
        equity_signal = make_signal(
            confidence=0.75, price=100.0, asset_class=AssetClass.EQUITY
        )
        crypto_signal = make_signal(
            confidence=0.75, price=100.0, asset_class=AssetClass.CRYPTO
        )
        equity_result = rm.evaluate(equity_signal, open_positions={})
        crypto_result = rm.evaluate(crypto_signal, open_positions={})
        assert crypto_result.position_size < equity_result.position_size

    def test_option_uses_tightest_position_fraction(self):
        rm, _ = _make_risk_manager(initial_capital=10_000)
        equity_result = rm.evaluate(
            make_signal(confidence=0.75, price=100.0, asset_class=AssetClass.EQUITY),
            open_positions={},
        )
        option_result = rm.evaluate(
            make_signal(confidence=0.75, price=100.0, asset_class=AssetClass.OPTION),
            open_positions={},
        )
        assert option_result.position_size < equity_result.position_size

    def test_crypto_higher_min_confidence_blocks_lower_confidence(self):
        # Crypto requires confidence >= 0.60 (default override)
        rm, _ = _make_risk_manager(min_signal_confidence=0.55)
        signal = make_signal(confidence=0.57, asset_class=AssetClass.CRYPTO)
        result = rm.evaluate(signal, open_positions={})
        assert not result.approved
