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
        keep_awake_until=None,
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


def test_unexpired_keep_awake_blocks():
    """A keep-awake deadline in the future pauses shutdown."""
    is_idle, reason = _decide(keep_awake_until=NOW + 600)
    assert not is_idle
    assert reason == "keep-awake is on"


def test_expired_keep_awake_does_not_block():
    """Once the keep-awake deadline has passed it is treated as off (bug-053)."""
    is_idle, reason = _decide(keep_awake_until=NOW - 1)
    assert is_idle, reason


@pytest.mark.parametrize(
    "overrides",
    [
        {"cfg": _cfg(idle_shutdown_enabled=False)},
        {"state_value": "offline"},
        {"state_value": "ollama_starting"},
        {"keep_awake_until": NOW + 3600},                     # keep-awake unexpired
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


# ── Keep-awake endpoint ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_keep_awake_endpoint_uses_default_ttl(client):
    """Enabling without a duration arms a bounded, default-length pause."""
    from app.config import settings
    from app.state import broker_state

    broker_state.keep_awake_until = None
    resp = await client.post("/api/idle/keep_awake", json={"enabled": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["keep_awake"] is True
    remaining = data["keep_awake_remaining_seconds"]
    assert remaining is not None
    # ~default minutes, allowing a little slack for clock drift during the call.
    assert settings.keep_awake_default_minutes * 60 - 5 < remaining <= settings.keep_awake_default_minutes * 60
    broker_state.keep_awake_until = None


@pytest.mark.asyncio
async def test_keep_awake_endpoint_caps_unbounded_request(client):
    """A huge ttl_minutes is clamped to the hard cap — no indefinite latch (bug-053)."""
    from app.config import settings
    from app.state import broker_state

    broker_state.keep_awake_until = None
    resp = await client.post(
        "/api/idle/keep_awake", json={"enabled": True, "ttl_minutes": 999_999}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["keep_awake"] is True
    assert data["keep_awake_remaining_seconds"] <= settings.keep_awake_max_minutes * 60
    broker_state.keep_awake_until = None


@pytest.mark.asyncio
async def test_keep_awake_endpoint_disable_clears(client):
    """Disabling clears the pause entirely."""
    from app.state import broker_state

    broker_state.set_keep_awake(True)
    resp = await client.post("/api/idle/keep_awake", json={"enabled": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["keep_awake"] is False
    assert data["keep_awake_remaining_seconds"] is None
    assert broker_state.keep_awake_until is None
