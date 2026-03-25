"""
Tests for bot/monitoring/health.py and bot/monitoring/alerts.py.

Coverage targets:
- HealthStatus and CheckResult behaviour
- DataFeedChecker, BrokerChecker, StrategyChecker (OK, DEGRADED, DOWN paths)
- HealthMonitor.run_once, callback dispatch, error isolation
- AlertSeverity ordering and channel threshold filtering
- AlertDispatcher: send, from_check_result, multi-channel routing
- ConsoleChannel / LogChannel do not raise
- default_dispatcher factory
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from bot.monitoring.alerts import (
    Alert,
    AlertDispatcher,
    AlertSeverity,
    ConsoleChannel,
    LogChannel,
    default_dispatcher,
)
from bot.monitoring.health import (
    BrokerChecker,
    CheckResult,
    DataFeedChecker,
    HealthChecker,
    HealthMonitor,
    HealthStatus,
    StrategyChecker,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def ok_probe():
    return lambda: True


@pytest.fixture()
def fail_probe():
    return lambda: False


@pytest.fixture()
def raise_probe():
    def _p():
        raise ConnectionError("host unreachable")
    return _p


# ── CheckResult ───────────────────────────────────────────────────────────────


class TestCheckResult:
    def test_is_healthy_ok(self):
        r = CheckResult(component="feed", status=HealthStatus.OK, message="ok")
        assert r.is_healthy is True

    def test_is_healthy_degraded(self):
        r = CheckResult(component="feed", status=HealthStatus.DEGRADED, message="slow")
        assert r.is_healthy is False

    def test_is_healthy_down(self):
        r = CheckResult(component="feed", status=HealthStatus.DOWN, message="dead")
        assert r.is_healthy is False

    def test_to_dict_includes_all_fields(self):
        r = CheckResult(component="broker", status=HealthStatus.OK, message="alive",
                        metadata={"latency_ms": 12})
        d = r.to_dict()
        assert d["component"] == "broker"
        assert d["status"] == "ok"
        assert d["latency_ms"] == 12
        assert "timestamp" in d


# ── DataFeedChecker ───────────────────────────────────────────────────────────


class TestDataFeedChecker:
    def test_ok(self, ok_probe):
        c = DataFeedChecker(probe=ok_probe)
        r = c.check()
        assert r.status == HealthStatus.OK
        assert c.name == "data_feed"

    def test_probe_returns_false_is_degraded(self, fail_probe):
        c = DataFeedChecker(probe=fail_probe)
        r = c.check()
        assert r.status == HealthStatus.DEGRADED

    def test_probe_raises_is_down(self, raise_probe):
        c = DataFeedChecker(probe=raise_probe)
        r = c.check()
        assert r.status == HealthStatus.DOWN
        assert "host unreachable" in r.message

    def test_custom_name(self, ok_probe):
        c = DataFeedChecker(probe=ok_probe, component_name="yahoo_feed")
        assert c.name == "yahoo_feed"


# ── BrokerChecker ─────────────────────────────────────────────────────────────


class TestBrokerChecker:
    def test_ok(self, ok_probe):
        c = BrokerChecker(probe=ok_probe)
        r = c.check()
        assert r.status == HealthStatus.OK

    def test_false_is_degraded(self, fail_probe):
        c = BrokerChecker(probe=fail_probe)
        r = c.check()
        assert r.status == HealthStatus.DEGRADED

    def test_raises_is_down(self, raise_probe):
        c = BrokerChecker(probe=raise_probe)
        r = c.check()
        assert r.status == HealthStatus.DOWN

    def test_default_name(self, ok_probe):
        assert BrokerChecker(probe=ok_probe).name == "broker"


# ── StrategyChecker ───────────────────────────────────────────────────────────


class TestStrategyChecker:
    def _recent(self) -> datetime:
        return datetime.now(tz=timezone.utc) - timedelta(seconds=10)

    def _old(self) -> datetime:
        return datetime.now(tz=timezone.utc) - timedelta(seconds=7200)

    def test_recent_signal_is_ok(self):
        c = StrategyChecker(last_signal_at=self._recent, max_silence_seconds=3600)
        r = c.check()
        assert r.status == HealthStatus.OK
        assert "silence_seconds" in r.metadata

    def test_old_signal_is_degraded(self):
        c = StrategyChecker(last_signal_at=self._old, max_silence_seconds=3600)
        r = c.check()
        assert r.status == HealthStatus.DEGRADED

    def test_no_signal_yet_is_degraded(self):
        c = StrategyChecker(last_signal_at=lambda: None)
        r = c.check()
        assert r.status == HealthStatus.DEGRADED
        assert "no signals" in r.message

    def test_raises_is_down(self):
        def bad():
            raise RuntimeError("state machine broken")
        c = StrategyChecker(last_signal_at=bad)
        r = c.check()
        assert r.status == HealthStatus.DOWN

    def test_default_name(self):
        c = StrategyChecker(last_signal_at=lambda: None)
        assert c.name == "strategy"


# ── HealthMonitor ─────────────────────────────────────────────────────────────


class TestHealthMonitor:
    def _monitor(self) -> HealthMonitor:
        return HealthMonitor(interval_seconds=0.05)

    def test_run_once_returns_results(self):
        m = self._monitor()
        m.register(DataFeedChecker(probe=lambda: True))
        m.register(BrokerChecker(probe=lambda: True))
        results = m.run_once()
        assert len(results) == 2
        assert all(r.status == HealthStatus.OK for r in results)

    def test_callback_called_per_result(self):
        m = self._monitor()
        m.register(DataFeedChecker(probe=lambda: True))
        m.register(BrokerChecker(probe=lambda: False))
        received = []
        m.add_callback(received.append)
        m.run_once()
        assert len(received) == 2
        statuses = {r.status for r in received}
        assert HealthStatus.OK in statuses
        assert HealthStatus.DEGRADED in statuses

    def test_callback_error_does_not_crash_monitor(self):
        m = self._monitor()
        m.register(DataFeedChecker(probe=lambda: True))
        def bad_callback(r):
            raise ValueError("boom")
        m.add_callback(bad_callback)
        # Should not raise
        results = m.run_once()
        assert len(results) == 1

    def test_background_thread_runs_and_stops(self):
        m = HealthMonitor(interval_seconds=0.02)
        counter = {"n": 0}
        m.register(DataFeedChecker(probe=lambda: True))
        m.add_callback(lambda r: counter.__setitem__("n", counter["n"] + 1))
        m.start()
        time.sleep(0.12)   # enough for ~3-5 ticks
        m.stop()
        assert counter["n"] >= 2

    def test_start_is_idempotent(self):
        m = HealthMonitor(interval_seconds=60)
        m.start()
        thread_a = m._thread
        m.start()  # second call should no-op
        assert m._thread is thread_a
        m.stop()

    def test_no_checkers_returns_empty(self):
        m = self._monitor()
        results = m.run_once()
        assert results == []


# ── AlertSeverity ─────────────────────────────────────────────────────────────


class TestAlertSeverity:
    def test_ordering(self):
        assert AlertSeverity.INFO < AlertSeverity.WARNING < AlertSeverity.CRITICAL

    def test_color_and_label(self):
        assert AlertSeverity.CRITICAL.color == "bold red"
        assert AlertSeverity.WARNING.label == "WARNING"


# ── Alert ─────────────────────────────────────────────────────────────────────


class TestAlert:
    def test_to_dict(self):
        a = Alert(severity=AlertSeverity.WARNING, title="Test", body="details",
                  source="broker", metadata={"extra": 1})
        d = a.to_dict()
        assert d["severity"] == "WARNING"
        assert d["extra"] == 1
        assert "timestamp" in d


# ── AlertDispatcher ───────────────────────────────────────────────────────────


class TestAlertDispatcher:
    def test_send_reaches_channel(self):
        d = AlertDispatcher(min_severity=AlertSeverity.INFO)
        mock_ch = MagicMock(spec=ConsoleChannel)
        d.add_channel(mock_ch)
        alert = Alert(severity=AlertSeverity.INFO, title="T", body="B")
        d.send(alert)
        mock_ch.send.assert_called_once_with(alert)

    def test_below_threshold_not_dispatched(self):
        d = AlertDispatcher(min_severity=AlertSeverity.WARNING)
        mock_ch = MagicMock(spec=ConsoleChannel)
        d.add_channel(mock_ch)
        d.send(Alert(severity=AlertSeverity.INFO, title="T", body="B"))
        mock_ch.send.assert_not_called()

    def test_critical_always_dispatched(self):
        # Even if min_severity is CRITICAL, CRITICAL alerts must still get through
        d = AlertDispatcher(min_severity=AlertSeverity.CRITICAL)
        mock_ch = MagicMock(spec=ConsoleChannel)
        d.add_channel(mock_ch)
        d.send(Alert(severity=AlertSeverity.CRITICAL, title="T", body="B"))
        mock_ch.send.assert_called_once()

    def test_multi_channel_both_receive(self):
        d = AlertDispatcher(min_severity=AlertSeverity.INFO)
        ch1 = MagicMock(spec=ConsoleChannel)
        ch2 = MagicMock(spec=LogChannel)
        d.add_channel(ch1)
        d.add_channel(ch2)
        d.send(Alert(severity=AlertSeverity.WARNING, title="T", body="B"))
        ch1.send.assert_called_once()
        ch2.send.assert_called_once()

    def test_channel_error_does_not_crash_dispatcher(self):
        d = AlertDispatcher(min_severity=AlertSeverity.INFO)
        bad_ch = MagicMock(spec=ConsoleChannel)
        bad_ch.send.side_effect = RuntimeError("channel down")
        d.add_channel(bad_ch)
        # Should not raise
        d.send(Alert(severity=AlertSeverity.WARNING, title="T", body="B"))

    def test_from_check_result_ok_not_dispatched(self):
        d = AlertDispatcher()
        mock_ch = MagicMock(spec=ConsoleChannel)
        d.add_channel(mock_ch)
        r = CheckResult(component="feed", status=HealthStatus.OK, message="fine")
        d.from_check_result(r)
        mock_ch.send.assert_not_called()

    def test_from_check_result_degraded_is_warning(self):
        received = []
        d = AlertDispatcher(min_severity=AlertSeverity.WARNING)
        d.add_channel(MagicMock(spec=AlertDispatcher))
        # Use a LogChannel spy instead
        d2 = AlertDispatcher(min_severity=AlertSeverity.INFO)
        d2.add_channel(type("Spy", (), {"send": lambda s, a: received.append(a)})())
        r = CheckResult(component="feed", status=HealthStatus.DEGRADED, message="slow")
        d2.from_check_result(r)
        assert received[0].severity == AlertSeverity.WARNING

    def test_from_check_result_down_is_critical(self):
        received = []
        d = AlertDispatcher(min_severity=AlertSeverity.INFO)
        d.add_channel(type("Spy", (), {"send": lambda s, a: received.append(a)})())
        r = CheckResult(component="broker", status=HealthStatus.DOWN, message="dead")
        d.from_check_result(r)
        assert received[0].severity == AlertSeverity.CRITICAL


# ── ConsoleChannel and LogChannel ─────────────────────────────────────────────


class TestBuiltinChannels:
    def test_console_channel_does_not_raise(self):
        ch = ConsoleChannel()
        a = Alert(severity=AlertSeverity.CRITICAL, title="T", body="B")
        ch.send(a)  # should not raise

    def test_log_channel_does_not_raise(self):
        ch = LogChannel()
        for sev in AlertSeverity:
            if sev.value > 0:
                ch.send(Alert(severity=sev, title="T", body="B"))


# ── default_dispatcher ────────────────────────────────────────────────────────


class TestDefaultDispatcher:
    def test_returns_dispatcher_with_two_channels(self):
        d = default_dispatcher()
        assert isinstance(d, AlertDispatcher)
        assert len(d._channels) == 2
        channel_types = {type(c) for c in d._channels}
        assert ConsoleChannel in channel_types
        assert LogChannel in channel_types
