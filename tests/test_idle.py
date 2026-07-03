"""Tests for idle auto-shutdown decision logic and poll-loop wiring."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.services.idle import should_idle_shutdown
from app.state import BrokerState, State

NOW = 100_000.0


def _cfg(**overrides) -> Settings:
    defaults = dict(
        idle_shutdown_enabled=True,
        idle_shutdown_minutes=30,
        idle_user_threshold_minutes=20,
        idle_post_wake_grace_minutes=15,
        idle_activity_poll_interval=60.0,
        idle_gpu_util_threshold=15,
        idle_consecutive_checks=2,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _idle_report(**overrides) -> dict:
    report = {
        "ok": True,
        "sessions": [
            {"id": 1, "station": "Console", "user": "matt", "state": "Active",
             "locked": False, "idle_seconds": 3600.0},
        ],
        "min_active_idle_seconds": 3600.0,
        "any_session_active": True,
        "gpu_util_percent": 2,
        "error": None,
    }
    report.update(overrides)
    return report


def _decide(**overrides) -> tuple[bool, str]:
    kwargs = dict(
        now=NOW,
        state_value="ready",
        keep_awake=False,
        active_streams=0,
        ready_since=NOW - 7200,          # ready for 2h
        last_llm_activity=NOW - 3600,    # last chat 1h ago
        wake_requested_at=None,
        report=_idle_report(),
        report_at=NOW - 10,
        cfg=_cfg(),
    )
    kwargs.update(overrides)
    return should_idle_shutdown(**kwargs)


def test_happy_path_idle():
    is_idle, reason = _decide()
    assert is_idle, reason


@pytest.mark.parametrize(
    "overrides",
    [
        {"cfg": _cfg(idle_shutdown_enabled=False)},
        {"state_value": "offline"},
        {"state_value": "ollama_starting"},
        {"keep_awake": True},
        {"active_streams": 1},
        {"last_llm_activity": NOW - 60},                      # fresh chat
        {"ready_since": NOW - 60, "last_llm_activity": None}, # just became ready
        {"ready_since": None},
        {"wake_requested_at": NOW - 120},                     # post-wake grace
        {"report": None},                                     # agent unreachable
        {"report_at": None},                                  # never fetched
        {"report_at": NOW - 500},                             # stale report
        {"report": _idle_report(ok=False, error="boom")},     # collection failed
        {"report": _idle_report(any_session_active=None)},    # unknown sessions
        {"report": _idle_report(gpu_util_percent=80)},        # gaming
    ],
    ids=[
        "disabled", "offline", "starting", "keep_awake", "active_stream",
        "fresh_llm_activity", "just_ready", "no_ready_since", "wake_grace",
        "no_report", "no_report_time", "stale_report", "report_not_ok",
        "unknown_sessions", "gpu_busy",
    ],
)
def test_blocked(overrides):
    is_idle, reason = _decide(**overrides)
    assert not is_idle, reason


def test_active_session_below_threshold_blocks():
    report = _idle_report()
    report["sessions"][0]["idle_seconds"] = 30.0  # user touched mouse 30s ago
    is_idle, _ = _decide(report=report)
    assert not is_idle


def test_active_session_unknown_idle_blocks():
    report = _idle_report()
    report["sessions"][0]["idle_seconds"] = None  # fail-safe: unknown = active
    is_idle, _ = _decide(report=report)
    assert not is_idle


def test_locked_session_does_not_block():
    report = _idle_report()
    report["sessions"][0]["locked"] = True
    report["sessions"][0]["idle_seconds"] = None
    is_idle, reason = _decide(report=report)
    assert is_idle, reason


def test_unknown_lock_state_blocks():
    report = _idle_report()
    report["sessions"][0]["locked"] = None
    report["sessions"][0]["idle_seconds"] = None
    is_idle, _ = _decide(report=report)
    assert not is_idle


def test_no_sessions_is_idle():
    """PC woken remotely, nobody logged in: LLM inactivity alone decides."""
    is_idle, reason = _decide(report=_idle_report(sessions=[], any_session_active=False))
    assert is_idle, reason


def test_gpu_check_disabled():
    is_idle, reason = _decide(
        report=_idle_report(gpu_util_percent=80),
        cfg=_cfg(idle_gpu_util_threshold=0),
    )
    assert is_idle, reason


# ── Poll-tick integration ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idle_tick_triggers_shutdown_once():
    state = BrokerState()
    state.state = State.ready
    state.ready_since = time.monotonic() - 7200
    state.last_llm_activity = time.monotonic() - 7200

    shutdown_mock = AsyncMock()
    activity_mock = AsyncMock(return_value=_idle_report())

    cfg_patch = {
        "idle_shutdown_enabled": True,
        "idle_consecutive_checks": 2,
        "idle_activity_poll_interval": 0.0,  # evaluate on every tick
    }
    from app.config import settings
    originals = {k: getattr(settings, k) for k in cfg_patch}
    for k, v in cfg_patch.items():
        setattr(settings, k, v)
    try:
        with patch("app.services.agent_client.get_activity", activity_mock), \
             patch("app.services.agent_client.shutdown", shutdown_mock):
            await state._idle_tick()   # strike 1
            shutdown_mock.assert_not_awaited()
            assert state.idle_strikes == 1

            await state._idle_tick()   # strike 2 -> shutdown
            shutdown_mock.assert_awaited_once()
            assert state.last_idle_shutdown is not None

            await state._idle_tick()   # re-trigger guard holds
            shutdown_mock.assert_awaited_once()
    finally:
        for k, v in originals.items():
            setattr(settings, k, v)
