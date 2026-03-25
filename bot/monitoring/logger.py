"""
Logging setup for the trading bot.

All layers import `get_logger` from here and call it once at module level:

    log = get_logger(__name__)
    log.info("signal_generated", symbol="AAPL", direction="long", confidence=0.82)

JSON logs go to the log file; human-readable Rich output goes to the console.
Never use print() anywhere in the codebase.
"""

import logging
import os
import sys
from pathlib import Path

import structlog
from rich.console import Console
from rich.logging import RichHandler

_configured = False


def configure_logging(log_level: str | None = None, log_file: str | None = None) -> None:
    """
    Call once at startup (bot/cli.py does this before anything else).
    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _configured
    if _configured:
        return

    level_name = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    log_path = log_file or os.getenv("LOG_FILE", "logs/trading.log")
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    # ── stdlib handlers ──────────────────────────────────────────────────────
    # Console: Rich-formatted, human-readable
    console_handler = RichHandler(
        console=Console(stderr=True),
        rich_tracebacks=True,
        markup=False,
        show_path=False,
    )
    console_handler.setLevel(level)

    # File: newline-delimited JSON for post-hoc analysis
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(level)

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[console_handler, file_handler],
    )

    # ── structlog processors ─────────────────────────────────────────────────
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Tell the file handler to render JSON
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=shared_processors,
        )
    )

    # Tell the console handler to render with Rich (plain key=value style)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=False),
            foreign_pre_chain=shared_processors,
        )
    )

    _configured = True


def get_logger(name: str = "bot") -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger. Call configure_logging() first."""
    return structlog.get_logger(name)
