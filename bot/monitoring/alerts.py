"""
Alert dispatcher — routes health check results and explicit alerts
to one or more configured channels.

Phase 1: console (Rich) only.
Phase 3+: add AWS SNS, Slack, PagerDuty channels by implementing AlertChannel.

Design
------
- `AlertChannel` ABC: one method — `send(alert)`.
- `Alert` dataclass: severity, title, body, source component, timestamp.
- `AlertDispatcher`: holds a list of channels and a severity threshold.
  Only alerts at or above the threshold are dispatched.
- `HealthMonitor` wires to `AlertDispatcher.from_check_result` so every
  DEGRADED/DOWN check automatically fires an alert.

Usage
-----
    dispatcher = AlertDispatcher(min_severity=AlertSeverity.WARNING)
    dispatcher.add_channel(ConsoleChannel())

    # Wire to health monitor
    monitor.add_callback(dispatcher.from_check_result)

    # Or send an explicit alert from anywhere
    dispatcher.send(Alert(severity=AlertSeverity.CRITICAL, title="Kill switch triggered", ...))
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from rich.console import Console
from rich.panel import Panel

from bot.monitoring.health import CheckResult, HealthStatus
from bot.monitoring.logger import get_logger

log = get_logger(__name__)

_console = Console(stderr=True)


# ── Alert severity ────────────────────────────────────────────────────────────


class AlertSeverity(int, Enum):
    """Ordered by urgency — higher value = more severe."""
    INFO     = 1
    WARNING  = 2
    CRITICAL = 3

    @property
    def color(self) -> str:
        return {
            AlertSeverity.INFO:     "cyan",
            AlertSeverity.WARNING:  "yellow",
            AlertSeverity.CRITICAL: "bold red",
        }[self]

    @property
    def label(self) -> str:
        return self.name


# ── Alert dataclass ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Alert:
    severity: AlertSeverity
    title: str
    body: str
    source: str = "unknown"
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.label,
            "title": self.title,
            "body": self.body,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            **self.metadata,
        }


# ── AlertChannel ABC ──────────────────────────────────────────────────────────


class AlertChannel(ABC):
    @abstractmethod
    def send(self, alert: Alert) -> None:
        """Deliver the alert. Must not raise — swallow and log on failure."""


# ── Built-in channels ─────────────────────────────────────────────────────────


class ConsoleChannel(AlertChannel):
    """
    Renders alerts as Rich panels to stderr.

    - INFO    → cyan panel
    - WARNING → yellow panel
    - CRITICAL → bold red panel, also logs at ERROR level

    Used in all phases. In Phase 3 it's supplemented by SNS/Slack,
    not replaced — the console log is always useful for debugging.
    """

    def send(self, alert: Alert) -> None:
        try:
            panel = Panel(
                f"[{alert.severity.color}]{alert.body}[/]",
                title=f"[{alert.severity.color}]{alert.severity.label}: {alert.title}[/]",
                subtitle=f"{alert.source} · {alert.timestamp.strftime('%H:%M:%S UTC')}",
                expand=False,
            )
            _console.print(panel)
        except Exception as exc:  # noqa: BLE001
            # Never let the alert channel crash the caller
            log.error("console_channel_error", error=str(exc))


class LogChannel(AlertChannel):
    """
    Emits alerts as structlog events.

    Useful as a fallback channel and for test assertions
    (callers can inspect the structured log output).
    """

    def send(self, alert: Alert) -> None:
        level = {
            AlertSeverity.INFO:     log.info,
            AlertSeverity.WARNING:  log.warning,
            AlertSeverity.CRITICAL: log.error,
        }[alert.severity]
        level("alert_dispatched", **alert.to_dict())


# ── AlertDispatcher ───────────────────────────────────────────────────────────


class AlertDispatcher:
    """
    Routes alerts to all registered channels.

    Only alerts whose severity >= `min_severity` are dispatched.
    CRITICAL alerts are always dispatched regardless of threshold.
    """

    def __init__(self, min_severity: AlertSeverity = AlertSeverity.WARNING) -> None:
        self.min_severity = min_severity
        self._channels: list[AlertChannel] = []

    def add_channel(self, channel: AlertChannel) -> None:
        self._channels.append(channel)

    def send(self, alert: Alert) -> None:
        if alert.severity < self.min_severity and alert.severity != AlertSeverity.CRITICAL:
            return
        log.debug("alert_routing", title=alert.title, severity=alert.severity.label,
                  channels=len(self._channels))
        for channel in list(self._channels):
            try:
                channel.send(alert)
            except Exception as exc:  # noqa: BLE001
                log.error("alert_channel_error", channel=type(channel).__name__, error=str(exc))

    def from_check_result(self, result: CheckResult) -> None:
        """
        Callback compatible with HealthMonitor.add_callback().
        Converts a CheckResult into an Alert and dispatches it.
        Healthy results are ignored (logged by the monitor, not worth alerting).
        """
        if result.status == HealthStatus.OK:
            return
        severity = (
            AlertSeverity.CRITICAL if result.status == HealthStatus.DOWN
            else AlertSeverity.WARNING
        )
        self.send(Alert(
            severity=severity,
            title=f"{result.component} {result.status.value}",
            body=result.message,
            source=result.component,
            timestamp=result.timestamp,
            metadata=result.metadata,
        ))


# ── Convenience factory ───────────────────────────────────────────────────────


def default_dispatcher(min_severity: AlertSeverity = AlertSeverity.WARNING) -> AlertDispatcher:
    """
    Returns an AlertDispatcher pre-wired with ConsoleChannel + LogChannel.
    This is the standard setup for Phase 1.
    """
    dispatcher = AlertDispatcher(min_severity=min_severity)
    dispatcher.add_channel(ConsoleChannel())
    dispatcher.add_channel(LogChannel())
    return dispatcher
