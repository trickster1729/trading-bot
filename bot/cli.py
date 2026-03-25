"""
CLI entrypoint — `bot` command.

Commands
--------
  bot backtest   Run a historical backtest
  bot report     Print performance summary from last run

Usage examples:
  bot backtest --symbols AAPL,MSFT --start 2023-01-01 --end 2024-01-01
  bot backtest --symbols BTC-USD --start 2022-01-01 --end 2023-01-01 --capital 5000
  bot report
"""

from __future__ import annotations

from datetime import datetime

import typer
from rich.console import Console

from bot.config import settings
from bot.monitoring.logger import configure_logging, get_logger

app = typer.Typer(
    name="bot",
    help="Algorithmic trading bot — Phase 1: CLI + shadow trading",
    add_completion=False,
)
console = Console()


# ── backtest command ───────────────────────────────────────────────────────────

@app.command()
def backtest(
    symbols: str = typer.Option(
        "AAPL,MSFT",
        "--symbols", "-s",
        help="Comma-separated list of symbols (e.g. AAPL,MSFT or BTC-USD)",
    ),
    start: datetime = typer.Option(
        datetime(2023, 1, 1),
        "--start",
        help="Backtest start date (YYYY-MM-DD)",
        formats=["%Y-%m-%d"],
    ),
    end: datetime = typer.Option(
        datetime(2024, 1, 1),
        "--end",
        help="Backtest end date (YYYY-MM-DD)",
        formats=["%Y-%m-%d"],
    ),
    capital: float = typer.Option(
        None,
        "--capital", "-c",
        help="Starting capital in USD (default: from INITIAL_CAPITAL env var)",
    ),
    interval: str = typer.Option(
        "1d",
        "--interval", "-i",
        help="Bar interval: 1d, 1h, 5m (Yahoo Finance notation)",
    ),
    rsi_period: int = typer.Option(14,  "--rsi-period",  help="RSI period"),
    sma_period: int = typer.Option(20,  "--sma-period",  help="SMA period"),
    oversold:   float = typer.Option(30.0, "--oversold",  help="RSI oversold threshold"),
    overbought: float = typer.Option(70.0, "--overbought", help="RSI overbought threshold"),
    log_level: str = typer.Option(None, "--log-level", help="DEBUG | INFO | WARNING"),
) -> None:
    """Run a historical backtest using the momentum strategy."""
    # Lazy imports to keep CLI startup fast
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.report import print_report
    from bot.data.yahoo import YahooLoader
    from bot.execution.paper import PaperBroker
    from bot.monitoring.metrics import PerformanceTracker
    from bot.risk.limits import RiskLimits
    from bot.risk.manager import RiskManager
    from bot.signals.momentum import MomentumStrategy

    configure_logging(log_level=log_level or settings.log_level)
    log = get_logger(__name__)

    initial_capital = capital or settings.initial_capital
    symbol_list = [s.strip().upper() for s in symbols.split(",")]

    log.info(
        "cli_backtest_invoked",
        symbols=symbol_list,
        start=start.date().isoformat(),
        end=end.date().isoformat(),
        initial_capital=initial_capital,
        interval=interval,
    )

    strategy = MomentumStrategy(
        params={
            "rsi_period": rsi_period,
            "sma_period": sma_period,
            "oversold":   oversold,
            "overbought": overbought,
        }
    )

    tracker = PerformanceTracker(initial_capital=initial_capital)
    limits  = RiskLimits(
        max_position_fraction=settings.max_position_fraction,
        max_drawdown_fraction=settings.max_drawdown_fraction,
    )
    risk    = RiskManager(limits=limits, tracker=tracker)
    broker  = PaperBroker()
    loader  = YahooLoader()

    engine = BacktestEngine(
        strategies=[strategy],
        loader=loader,
        broker=broker,
        tracker=tracker,
        risk=risk,
    )

    result = engine.run(symbols=symbol_list, start=start, end=end, interval=interval)
    print_report(result, tracker)


# ── report command ─────────────────────────────────────────────────────────────

@app.command()
def report() -> None:
    """
    Print the performance summary from the most recent run.
    (Phase 1: re-runs a default backtest. Phase 2+: reads from DB.)
    """
    console.print(
        "[yellow]Tip:[/yellow] Use [bold]bot backtest[/bold] to run a backtest "
        "and see the full report inline.\n"
        "Persistent run history (DB-backed) coming in Phase 2."
    )


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
