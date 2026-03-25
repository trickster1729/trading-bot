"""
Centralised configuration.

All environment variables are read here — nowhere else in the codebase
should call os.getenv() directly. This makes the config surface explicit,
testable, and easy to swap for AWS Secrets Manager in Phase 3+.

Usage:
    from bot.config import settings
    print(settings.initial_capital)

Phase 3+ extension: replace Settings with a Pydantic BaseSettings model
that can load from AWS Secrets Manager or Parameter Store automatically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env file if present (safe no-op if missing)
load_dotenv()


@dataclass
class Settings:
    # ── Trading mode ──────────────────────────────────────────────────────────
    # "shadow" = historical replay, paper PnL
    # "paper"  = live prices, paper orders  (Phase 2)
    # "live"   = live prices, real orders   (Phase 3)
    mode: str = field(default_factory=lambda: os.getenv("BOT_MODE", "shadow"))

    # ── Capital ───────────────────────────────────────────────────────────────
    initial_capital: float = field(
        default_factory=lambda: float(os.getenv("INITIAL_CAPITAL", "10000"))
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file:  str = field(default_factory=lambda: os.getenv("LOG_FILE", "logs/trading.log"))

    # ── Broker credentials (Phase 2+) ─────────────────────────────────────────
    alpaca_api_key:    str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    alpaca_api_secret: str = field(default_factory=lambda: os.getenv("ALPACA_API_SECRET", ""))
    alpaca_base_url:   str = field(
        default_factory=lambda: os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    )

    binance_api_key:    str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    binance_api_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", ""))

    # ── Risk defaults (overridable via env) ───────────────────────────────────
    max_position_fraction: float = field(
        default_factory=lambda: float(os.getenv("MAX_POSITION_FRACTION", "0.02"))
    )
    max_drawdown_fraction: float = field(
        default_factory=lambda: float(os.getenv("MAX_DRAWDOWN_FRACTION", "0.10"))
    )

    # ── Data ──────────────────────────────────────────────────────────────────
    data_cache_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_CACHE_DIR", "data/cache"))
    )

    def validate(self) -> None:
        """Raise if any required setting is missing or invalid."""
        valid_modes = {"shadow", "paper", "live"}
        if self.mode not in valid_modes:
            raise ValueError(f"BOT_MODE must be one of {valid_modes}, got {self.mode!r}")

        if self.mode in {"paper", "live"}:
            if not self.alpaca_api_key:
                raise ValueError("ALPACA_API_KEY is required in paper/live mode")
            if not self.alpaca_api_secret:
                raise ValueError("ALPACA_API_SECRET is required in paper/live mode")

        if not 0 < self.max_position_fraction <= 0.10:
            raise ValueError(f"MAX_POSITION_FRACTION must be in (0, 0.10], got {self.max_position_fraction}")

        if not 0 < self.max_drawdown_fraction <= 0.50:
            raise ValueError(f"MAX_DRAWDOWN_FRACTION must be in (0, 0.50], got {self.max_drawdown_fraction}")


# Singleton — import this everywhere
settings = Settings()
