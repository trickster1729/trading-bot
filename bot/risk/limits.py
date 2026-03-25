"""
Risk parameter configuration.

All limits are loaded once at startup from environment variables (via config.py)
or passed explicitly in tests. The RiskManager reads from a RiskLimits instance —
never from env directly — so limits can be overridden per-strategy or per-asset-class
in Phase 3+ without changing the manager logic.

Phase 4 extension point: subclass RiskLimits per asset class (EquityLimits,
CryptoLimits, OptionsLimits) and pass the right one to the manager.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.signals.base import AssetClass


@dataclass
class RiskLimits:
    """
    Configurable hard limits enforced by RiskManager.

    All monetary values are in account currency (USD assumed).
    All percentage values are fractions (0.02 = 2%).
    """

    # ── Per-trade limits ──────────────────────────────────────────────────────

    # Maximum fraction of account to risk on a single trade (fixed fractional)
    max_position_fraction: float = 0.02        # 2% — non-negotiable Phase 1 rule

    # Minimum confidence a Signal must have to be acted on
    min_signal_confidence: float = 0.55

    # Maximum number of concurrent open positions (across all symbols)
    max_open_positions: int = 10

    # Maximum number of open positions in a single symbol
    max_positions_per_symbol: int = 1

    # ── Account-level limits ──────────────────────────────────────────────────

    # Halt all trading if account drawdown exceeds this fraction
    max_drawdown_fraction: float = 0.10        # 10% — kill switch threshold

    # Daily loss limit: halt for the day if daily PnL drops below this fraction
    max_daily_loss_fraction: float = 0.03      # 3% of account per day

    # ── Per-asset-class overrides ─────────────────────────────────────────────
    # Maps AssetClass → dict of field overrides.
    # Example: crypto gets a tighter position fraction due to higher volatility.
    # In Phase 3+, load these from config rather than hardcoding.
    asset_class_overrides: dict[AssetClass, dict[str, Any]] = field(
        default_factory=lambda: {
            AssetClass.CRYPTO: {
                "max_position_fraction": 0.015,   # tighter for crypto volatility
                "min_signal_confidence": 0.60,
            },
            AssetClass.OPTION: {
                "max_position_fraction": 0.01,    # options can go to zero
                "min_signal_confidence": 0.70,
            },
        }
    )

    def for_asset_class(self, asset_class: AssetClass) -> "RiskLimits":
        """
        Return a copy of these limits with per-asset-class overrides applied.
        Called by RiskManager before evaluating each signal.
        """
        overrides = self.asset_class_overrides.get(asset_class, {})
        if not overrides:
            return self

        import dataclasses
        return dataclasses.replace(self, **overrides)
