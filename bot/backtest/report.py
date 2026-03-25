"""
Backtest report — renders performance metrics to the terminal.

Uses Rich for a clean, readable CLI output. All numbers come from
PerformanceTracker.summary() so the report is consistent whether called
from the CLI or from a test.

Phase 4 extension: emit the same data as JSON/Prometheus metrics for
Grafana dashboard rendering.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from bot.backtest.engine import BacktestResult
from bot.monitoring.metrics import PerformanceTracker

console = Console()


def print_report(result: BacktestResult, tracker: PerformanceTracker) -> None:
    """Print a formatted performance report to the terminal."""
    p = tracker.summary()

    # ── Header ────────────────────────────────────────────────────────────────
    console.print(
        Panel(
            f"[bold]Backtest Report[/bold]\n"
            f"Symbols : {', '.join(result.symbols)}\n"
            f"Period  : {result.start.date()} → {result.end.date()}\n"
            f"Strategy: {', '.join(result.strategy_names)}",
            style="cyan",
        )
    )

    # ── Activity summary ──────────────────────────────────────────────────────
    activity = Table(show_header=False, box=None, padding=(0, 2))
    activity.add_column("Metric", style="dim")
    activity.add_column("Value", justify="right")
    activity.add_row("Bars processed",    str(result.total_bars))
    activity.add_row("Signals generated", str(result.signals_generated))
    activity.add_row("Orders submitted",  str(result.orders_submitted))
    activity.add_row("Orders filled",     str(result.orders_filled))
    activity.add_row("Closed trades",     str(p["trade_count"]))
    console.print(Panel(activity, title="Activity", style="blue"))

    # ── Performance metrics ───────────────────────────────────────────────────
    perf = Table(show_header=False, box=None, padding=(0, 2))
    perf.add_column("Metric", style="dim")
    perf.add_column("Value", justify="right")

    pnl_color = "green" if p["total_pnl"] >= 0 else "red"
    perf.add_row("Initial capital",  f"${p['initial_capital']:,.2f}")
    perf.add_row("Final capital",    f"${p['current_capital']:,.2f}")
    perf.add_row(
        "Total PnL",
        f"[{pnl_color}]${p['total_pnl']:+,.2f} ({p['total_return_pct']:+.2f}%)[/{pnl_color}]",
    )
    perf.add_row("Win rate",         f"{p['win_rate']:.1f}%")
    perf.add_row("Sharpe ratio",     f"{p['sharpe_ratio']:.2f}")
    perf.add_row("Max drawdown",     f"{p['max_drawdown_pct']:.2f}%")
    console.print(Panel(perf, title="Performance", style="green" if p["total_pnl"] >= 0 else "red"))

    # ── Trade list (last 10) ──────────────────────────────────────────────────
    if tracker.trades:
        trade_table = Table(show_header=True, style="dim")
        trade_table.add_column("Symbol")
        trade_table.add_column("Side")
        trade_table.add_column("Entry", justify="right")
        trade_table.add_column("Exit", justify="right")
        trade_table.add_column("Qty", justify="right")
        trade_table.add_column("PnL", justify="right")

        for t in tracker.trades[-10:]:
            pnl_str = f"${t.pnl:+.2f}"
            color = "green" if t.pnl >= 0 else "red"
            trade_table.add_row(
                t.symbol,
                t.side,
                f"${t.entry_price:.2f}",
                f"${t.exit_price:.2f}",
                f"{t.qty:.2f}",
                f"[{color}]{pnl_str}[/{color}]",
            )
        console.print(Panel(trade_table, title="Last 10 Trades", style="blue"))
