"""
Risk Manager — the gatekeeper between signals and execution.

Every signal passes through RiskManager.evaluate() before an order is placed.
The manager answers two questions:
  1. Should we act on this signal at all? (approve / block)
  2. If yes, how large should the position be?

Design for scale
----------------
- Stateless evaluation: the manager holds no open-position state itself.
  Position state is passed in by the caller (backtest engine or live loop).
  This makes it trivially testable and safe to call from multiple threads.
- Per-asset-class limits: RiskLimits.for_asset_class() applies tighter or
  looser rules per asset type without branching in the manager.
- Kill-switch flag: once tripped, evaluate() blocks ALL signals until reset.
  Phase 3+ will expose a REST endpoint to reset remotely.
- Every decision — approve OR block — is logged with full context so we can
  audit exactly why a trade was or wasn't taken.

Phase 3+ extension points
--------------------------
- Add correlation checks (block if new signal is too correlated with open positions)
- Add VaR-based position sizing as an alternative to fixed fractional
- Add time-of-day rules (no entries in last 30 min of equities session)
- Add sector concentration limits
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from bot.monitoring.logger import get_logger
from bot.monitoring.metrics import PerformanceTracker
from bot.risk.limits import RiskLimits
from bot.signals.base import Direction, Signal

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


# ── Decision types ────────────────────────────────────────────────────────────

class RiskDecision(str, Enum):
    APPROVED = "approved"
    BLOCKED  = "blocked"


@dataclass
class RiskResult:
    """
    The risk manager's verdict on a signal.

    Fields
    ------
    decision       : APPROVED or BLOCKED
    position_size  : number of units/shares/contracts to trade (0 if blocked)
    reason         : human-readable explanation (always populated — logged)
    signal         : the original signal being evaluated
    account_value  : account value at time of evaluation (for audit trail)
    """
    decision:      RiskDecision
    position_size: float
    reason:        str
    signal:        Signal
    account_value: float

    @property
    def approved(self) -> bool:
        return self.decision == RiskDecision.APPROVED


# ── Risk Manager ──────────────────────────────────────────────────────────────

class RiskManager:
    """
    Evaluates signals against configurable risk limits and returns a RiskResult.

    Args:
        limits  : RiskLimits instance (loaded from config at startup)
        tracker : PerformanceTracker — read-only source of truth for account state
    """

    def __init__(self, limits: RiskLimits, tracker: PerformanceTracker) -> None:
        self.limits  = limits
        self.tracker = tracker
        self._kill_switch_active = False

    # ── Kill switch ───────────────────────────────────────────────────────────

    def trip_kill_switch(self, reason: str) -> None:
        """Halt all trading. Called when drawdown limit is breached."""
        self._kill_switch_active = True
        log.error(
            "kill_switch_tripped",
            reason=reason,
            current_drawdown_pct=round(self.tracker.current_drawdown_pct * 100, 2),
            account_value=round(self.tracker.current_capital, 2),
        )

    def reset_kill_switch(self) -> None:
        """Re-enable trading (manual operator action only)."""
        self._kill_switch_active = False
        log.warning("kill_switch_reset", account_value=round(self.tracker.current_capital, 2))

    @property
    def is_halted(self) -> bool:
        return self._kill_switch_active

    # ── Main evaluation entry point ───────────────────────────────────────────

    def evaluate(
        self,
        signal: Signal,
        open_positions: dict[str, int],   # symbol → number of open positions
        price: float | None = None,
    ) -> RiskResult:
        """
        Evaluate a signal and return a RiskResult.

        Args:
            signal         : the signal to evaluate
            open_positions : current open positions (symbol → count).
                             Passed by caller so the manager stays stateless.
            price          : override price for position sizing (default: signal.price)

        Returns:
            RiskResult with decision, position_size, and reason.
        """
        account_value = self.tracker.current_capital
        effective_price = price or signal.price
        limits = self.limits.for_asset_class(signal.asset_class)

        def _block(reason: str) -> RiskResult:
            log.warning(
                "risk_check_blocked",
                symbol=signal.symbol,
                strategy=signal.strategy,
                direction=signal.direction.value,
                reason=reason,
                account_value=round(account_value, 2),
                current_drawdown_pct=round(self.tracker.current_drawdown_pct * 100, 2),
            )
            return RiskResult(
                decision=RiskDecision.BLOCKED,
                position_size=0.0,
                reason=reason,
                signal=signal,
                account_value=account_value,
            )

        # ── Gate 1: Kill switch ───────────────────────────────────────────────
        if self._kill_switch_active:
            return _block("kill_switch_active")

        # ── Gate 2: Non-actionable signal ─────────────────────────────────────
        if not signal.is_actionable():
            return _block("signal_direction_is_flat")

        # ── Gate 3: Minimum confidence ────────────────────────────────────────
        if signal.confidence < limits.min_signal_confidence:
            return _block(
                f"confidence_{signal.confidence:.3f}_below_minimum_{limits.min_signal_confidence}"
            )

        # ── Gate 4: Account-level drawdown (trip kill switch if breached) ─────
        drawdown = self.tracker.current_drawdown_pct
        if drawdown >= limits.max_drawdown_fraction:
            self.trip_kill_switch(
                f"drawdown_{drawdown*100:.2f}pct_exceeds_limit_{limits.max_drawdown_fraction*100:.0f}pct"
            )
            return _block("max_drawdown_breached_kill_switch_tripped")

        # ── Gate 5: Max open positions ────────────────────────────────────────
        total_open = sum(open_positions.values())
        if total_open >= limits.max_open_positions:
            return _block(
                f"max_open_positions_{limits.max_open_positions}_reached"
            )

        # ── Gate 6: Max positions per symbol ─────────────────────────────────
        symbol_open = open_positions.get(signal.symbol, 0)
        if symbol_open >= limits.max_positions_per_symbol:
            return _block(
                f"max_positions_per_symbol_{limits.max_positions_per_symbol}_reached_for_{signal.symbol}"
            )

        # ── Position sizing: fixed fractional ────────────────────────────────
        # Risk `max_position_fraction` of account per trade.
        # position_size = (account_value * fraction) / price
        risk_dollars = account_value * limits.max_position_fraction
        position_size = risk_dollars / effective_price if effective_price > 0 else 0.0

        log.info(
            "risk_check_passed",
            symbol=signal.symbol,
            strategy=signal.strategy,
            direction=signal.direction.value,
            confidence=round(signal.confidence, 3),
            position_size=round(position_size, 4),
            risk_dollars=round(risk_dollars, 2),
            account_value=round(account_value, 2),
            current_drawdown_pct=round(drawdown * 100, 2),
            open_positions=total_open,
        )

        return RiskResult(
            decision=RiskDecision.APPROVED,
            position_size=round(position_size, 4),
            reason="all_checks_passed",
            signal=signal,
            account_value=account_value,
        )
