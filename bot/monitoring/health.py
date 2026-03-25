"""
Health monitoring — heartbeat loop for Phase 1+.

Runs a set of registered health checks on a configurable interval and
dispatches results to the alert system.  Each check is a small callable
that knows how to probe one component (data feed, broker connection,
strategy liveness) and returns a CheckResult.

Design
------
- Checks are registered via `HealthMonitor.register(checker)`.
- The monitor runs in a background thread (daemon=True so it doesn't
  block process exit).
- Results are logged via structlog and forwarded to the AlertDispatcher.
- No external dependencies (no Prometheus, no HTTP endpoint) in Phase 1.
  Phase 4 can swap in a Prometheus push-gateway by adding a new checker.

Usage
-----
    monitor = HealthMonitor(interval_seconds=60)
    monitor.register(DataFeedChecker(loader=yahoo_loader))
    monitor.register(BrokerChecker(broker=paper_broker))
    monitor.start()          # non-blocking
    ...
    monitor.stop()
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from bot.monitoring.logger import get_logger

log = get_logger(__name__)


# ── Status vocabulary ─────────────────────────────────────────────────────────


class HealthStatus(str, Enum):
    OK       = "ok"        # component is fully operational
    DEGRADED = "degraded"  # partial failure, still functioning
    DOWN     = "down"      # component is unreachable / failed


# ── CheckResult dataclass ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckResult:
    component: str
    status: HealthStatus
    message: str
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    metadata: dict = field(default_factory=dict)

    @property
    def is_healthy(self) -> bool:
        return self.status == HealthStatus.OK

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "status": self.status.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            **self.metadata,
        }


# ── HealthChecker ABC ─────────────────────────────────────────────────────────


class HealthChecker(ABC):
    """Base class for a single component health check."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for the component (e.g. 'data_feed', 'broker')."""

    @abstractmethod
    def check(self) -> CheckResult:
        """Probe the component and return a CheckResult. Must not raise."""


# ── Built-in checkers (Phase 1) ───────────────────────────────────────────────


class DataFeedChecker(HealthChecker):
    """
    Checks that the data loader can return at least one recent bar.

    Pass a callable that fetches a single test bar: () -> bool.
    This keeps the checker decoupled from a specific DataLoader implementation.
    """

    def __init__(
        self,
        probe: Callable[[], bool],
        component_name: str = "data_feed",
    ) -> None:
        self._probe = probe
        self._name = component_name

    @property
    def name(self) -> str:
        return self._name

    def check(self) -> CheckResult:
        try:
            ok = self._probe()
            if ok:
                return CheckResult(component=self.name, status=HealthStatus.OK,
                                   message="data feed reachable")
            return CheckResult(component=self.name, status=HealthStatus.DEGRADED,
                               message="probe returned False — no data received")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(component=self.name, status=HealthStatus.DOWN,
                               message=f"probe raised: {exc}")


class BrokerChecker(HealthChecker):
    """
    Checks broker connectivity.

    For PaperBroker this is always OK (local, in-process).
    For AlpacaBroker/BinanceBroker in Phase 2+ it will do a lightweight
    account ping. Accepts a callable probe so live brokers can inject
    their own ping without changing this class.
    """

    def __init__(
        self,
        probe: Callable[[], bool],
        component_name: str = "broker",
    ) -> None:
        self._probe = probe
        self._name = component_name

    @property
    def name(self) -> str:
        return self._name

    def check(self) -> CheckResult:
        try:
            ok = self._probe()
            if ok:
                return CheckResult(component=self.name, status=HealthStatus.OK,
                                   message="broker reachable")
            return CheckResult(component=self.name, status=HealthStatus.DEGRADED,
                               message="broker probe returned False")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(component=self.name, status=HealthStatus.DOWN,
                               message=f"broker probe raised: {exc}")


class StrategyChecker(HealthChecker):
    """
    Checks that the strategy produced at least one signal in the last window.

    `last_signal_at` should be a callable that returns the datetime of the
    most recent signal emission (or None if no signal has ever been produced).
    `max_silence_seconds` defines how long without a signal before DEGRADED.
    """

    def __init__(
        self,
        last_signal_at: Callable[[], datetime | None],
        max_silence_seconds: float = 3600,
        component_name: str = "strategy",
    ) -> None:
        self._last_signal_at = last_signal_at
        self._max_silence = max_silence_seconds
        self._name = component_name

    @property
    def name(self) -> str:
        return self._name

    def check(self) -> CheckResult:
        try:
            ts = self._last_signal_at()
            if ts is None:
                return CheckResult(
                    component=self.name,
                    status=HealthStatus.DEGRADED,
                    message="no signals emitted yet",
                )
            now = datetime.now(tz=timezone.utc)
            silence = (now - ts).total_seconds()
            if silence > self._max_silence:
                return CheckResult(
                    component=self.name,
                    status=HealthStatus.DEGRADED,
                    message=f"no signal for {silence:.0f}s (threshold {self._max_silence}s)",
                    metadata={"silence_seconds": round(silence, 1)},
                )
            return CheckResult(
                component=self.name,
                status=HealthStatus.OK,
                message=f"last signal {silence:.0f}s ago",
                metadata={"silence_seconds": round(silence, 1)},
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult(component=self.name, status=HealthStatus.DOWN,
                               message=f"strategy checker raised: {exc}")


# ── HealthMonitor ─────────────────────────────────────────────────────────────


class HealthMonitor:
    """
    Runs registered health checkers on a fixed interval in a background thread.

    Results are:
    1. Logged via structlog (always)
    2. Forwarded to `on_result` callbacks (optional) — used by AlertDispatcher

    Thread safety: checkers are called sequentially within the interval tick.
    Adding/removing checkers while running is safe (list copy per tick).
    """

    def __init__(self, interval_seconds: float = 60.0) -> None:
        self.interval_seconds = interval_seconds
        self._checkers: list[HealthChecker] = []
        self._callbacks: list[Callable[[CheckResult], None]] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def register(self, checker: HealthChecker) -> None:
        self._checkers.append(checker)

    def add_callback(self, fn: Callable[[CheckResult], None]) -> None:
        """Register a function called on every CheckResult (e.g. alert dispatcher)."""
        self._callbacks.append(fn)

    def start(self) -> None:
        """Start the heartbeat loop in a daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="health-monitor", daemon=True
        )
        self._thread.start()
        log.info("health_monitor_started", interval_seconds=self.interval_seconds)

    def stop(self) -> None:
        """Signal the heartbeat loop to stop. Returns after the thread exits."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self.interval_seconds + 5)
        log.info("health_monitor_stopped")

    def run_once(self) -> list[CheckResult]:
        """Run all checks synchronously and return results. Useful for tests."""
        checkers = list(self._checkers)
        results = []
        for checker in checkers:
            result = checker.check()
            self._emit(result)
            results.append(result)
        return results

    # ── Internal ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(timeout=self.interval_seconds)

    def _emit(self, result: CheckResult) -> None:
        level = {
            HealthStatus.OK:       log.debug,
            HealthStatus.DEGRADED: log.warning,
            HealthStatus.DOWN:     log.error,
        }[result.status]
        level("health_check", **result.to_dict())

        for cb in list(self._callbacks):
            try:
                cb(result)
            except Exception as exc:  # noqa: BLE001
                log.error("health_callback_error", error=str(exc))
