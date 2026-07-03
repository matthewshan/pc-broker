"""Idle auto-shutdown decision logic.

Pure function so it is trivially unit-testable. The guiding rule: every
ambiguous or missing signal means "user is active" — the broker must never
shut the PC down on uncertain data.
"""
from __future__ import annotations

from typing import Any, Optional

from app.config import Settings


def _session_in_use(session: dict[str, Any], user_threshold_s: float) -> bool:
    """A session blocks shutdown if it is Active, not locked, and recently used.

    Unknown lock state or unknown idle time counts as in-use (fail-safe).
    Locked or disconnected sessions do not block: the user is not at the desk.
    """
    if session.get("state") != "Active":
        return False
    if session.get("locked") is True:
        return False
    idle = session.get("idle_seconds")
    if idle is None:
        return True
    return idle < user_threshold_s


def should_idle_shutdown(
    *,
    now: float,
    state_value: str,
    keep_awake: bool,
    active_streams: int,
    ready_since: Optional[float],
    last_llm_activity: Optional[float],
    wake_requested_at: Optional[float],
    report: Optional[dict[str, Any]],
    report_at: Optional[float],
    cfg: Settings,
) -> tuple[bool, str]:
    """Return (is_idle, reason). All monotonic-clock inputs share `now`'s epoch."""
    if not cfg.idle_shutdown_enabled:
        return False, "idle shutdown disabled"
    if state_value != "ready":
        return False, f"state is {state_value}, not ready"
    if keep_awake:
        return False, "keep-awake is on"
    if active_streams > 0:
        return False, f"{active_streams} LLM stream(s) in flight"

    if ready_since is None:
        return False, "no ready_since timestamp"
    reference = max(
        ready_since,
        last_llm_activity or 0.0,
        wake_requested_at or 0.0,
    )
    llm_idle = now - reference
    if llm_idle < cfg.idle_shutdown_minutes * 60:
        return False, f"LLM activity {llm_idle:.0f}s ago"

    if wake_requested_at is not None and now - wake_requested_at < cfg.idle_post_wake_grace_minutes * 60:
        return False, "within post-wake grace period"

    if report is None:
        return False, "no activity report from agent"
    if report_at is None or now - report_at > 2 * cfg.idle_activity_poll_interval:
        return False, "activity report is stale"
    if not report.get("ok"):
        return False, f"agent activity collection failed: {report.get('error')}"
    if report.get("any_session_active") is None:
        return False, "session activity unknown"

    threshold_s = cfg.idle_user_threshold_minutes * 60
    for session in report.get("sessions", []):
        if _session_in_use(session, threshold_s):
            return False, f"session {session.get('id')} ({session.get('user')}) in use"

    gpu = report.get("gpu_util_percent")
    if cfg.idle_gpu_util_threshold > 0 and isinstance(gpu, (int, float)) and gpu > cfg.idle_gpu_util_threshold:
        return False, f"GPU busy ({gpu}%)"

    return True, f"idle (LLM {llm_idle:.0f}s, no user activity)"
