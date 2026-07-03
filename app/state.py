"""Broker state machine.

States
------
offline          PC is not reachable.
waking           Wake-on-LAN packet sent; waiting for the PC to respond.
host_up          PC is reachable on the network (legacy; the poll loop now
                 lands on ollama_starting/ready instead).
ollama_starting  PC is reachable but Ollama is not answering yet.
ready            PC is reachable and Ollama is healthy.
timeout          Wake attempt exceeded the configured timeout.
error            Unexpected error during polling.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from app import events as event_log
from app.config import settings
from app.services import agent_client
from app.services import idle as idle_svc
from app.services import ollama as ollama_svc
from app.services import ping as ping_svc
from app.services import wol as wol_svc

logger = logging.getLogger(__name__)

_MODEL_CACHE_TTL = 60.0
_IDLE_RETRIGGER_GUARD = 300.0  # s to wait after a triggered shutdown


class State(str, Enum):
    offline = "offline"
    waking = "waking"
    host_up = "host_up"
    ollama_starting = "ollama_starting"
    ready = "ready"
    timeout = "timeout"
    error = "error"


class BrokerState:
    def __init__(self) -> None:
        self.state: State = State.offline
        self.pc_reachable: bool = False
        self.last_seen: Optional[str] = None
        self.last_wake_request: Optional[str] = None
        self.ollama_reachable: bool = False
        self.ollama_last_checked: Optional[str] = None
        self.models: list[dict[str, Any]] = []
        self._wake_requested_at: Optional[float] = None
        self._models_refreshed_at: Optional[float] = None
        self.last_llm_activity: Optional[float] = None  # time.monotonic()
        self.active_llm_streams: int = 0
        self.keep_awake: bool = False
        self.ready_since: Optional[float] = None  # time.monotonic()
        self.last_idle_shutdown: Optional[str] = None
        self.idle_strikes: int = 0
        self._activity_report: Optional[dict[str, Any]] = None
        self._activity_report_at: Optional[float] = None
        self._idle_triggered_at: Optional[float] = None
        self._lock = asyncio.Lock()
        self._poll_task: Optional[asyncio.Task] = None

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def note_llm_activity(self) -> None:
        self.last_llm_activity = time.monotonic()

    async def start_background_poll(self) -> None:
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop_background_poll(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._refresh()
            except Exception:
                logger.exception("Unexpected error in poll loop")
            try:
                await self._idle_tick()
            except Exception:
                logger.exception("Unexpected error in idle tick")
            await asyncio.sleep(settings.poll_interval)

    async def _refresh(self) -> None:
        pc_up = await ping_svc.is_reachable(settings.pc_host, settings.pc_reachability_port)
        ollama_up = await ollama_svc.is_healthy() if pc_up else False

        models: Optional[list[dict[str, Any]]] = None
        if ollama_up and self._models_cache_stale():
            try:
                models = await ollama_svc.list_models()
            except Exception:
                logger.warning("Ollama healthy but model listing failed", exc_info=True)

        async with self._lock:
            self.pc_reachable = pc_up
            self.ollama_reachable = ollama_up
            if pc_up:
                self.last_seen = self._now()
                self.ollama_last_checked = self._now()
            else:
                self.models = []
                self._models_refreshed_at = None
            if models is not None:
                self.models = models
                self._models_refreshed_at = time.monotonic()

            previous = self.state
            if pc_up:
                self.state = State.ready if ollama_up else State.ollama_starting
            elif self.state == State.waking and self._wake_timed_out():
                self.state = State.timeout
            elif self.state not in (State.waking, State.timeout, State.error):
                self.state = State.offline

            if previous != self.state:
                if self.state is State.ready:
                    self.ready_since = time.monotonic()
                elif previous is State.ready:
                    self.ready_since = None
                    self.idle_strikes = 0
                event_log.add_event(
                    "state_change",
                    f"State changed from {previous.value} to {self.state.value}",
                )
                logger.info("State: %s -> %s", previous.value, self.state.value)

    async def _idle_tick(self) -> None:
        """Evaluate idle auto-shutdown once per activity-poll interval."""
        if not settings.idle_shutdown_enabled or self.state is not State.ready:
            return
        if (
            self._idle_triggered_at is not None
            and time.monotonic() - self._idle_triggered_at < _IDLE_RETRIGGER_GUARD
        ):
            return
        # Strike cadence == activity-poll cadence: only evaluate on a fresh report.
        if (
            self._activity_report_at is not None
            and time.monotonic() - self._activity_report_at < settings.idle_activity_poll_interval
        ):
            return

        self._activity_report = await agent_client.get_activity()
        self._activity_report_at = time.monotonic()

        is_idle, reason = idle_svc.should_idle_shutdown(
            now=time.monotonic(),
            state_value=self.state.value,
            keep_awake=self.keep_awake,
            active_streams=self.active_llm_streams,
            ready_since=self.ready_since,
            last_llm_activity=self.last_llm_activity,
            wake_requested_at=self._wake_requested_at,
            report=self._activity_report,
            report_at=self._activity_report_at,
            cfg=settings,
        )
        if not is_idle:
            if self.idle_strikes:
                logger.info("Idle strike reset: %s", reason)
            self.idle_strikes = 0
            return

        self.idle_strikes += 1
        logger.info(
            "Idle strike %d/%d: %s", self.idle_strikes, settings.idle_consecutive_checks, reason
        )
        if self.idle_strikes < settings.idle_consecutive_checks:
            return

        self.idle_strikes = 0
        self._idle_triggered_at = time.monotonic()
        self.last_idle_shutdown = self._now()
        event_log.add_event("idle_shutdown", f"Idle auto-shutdown triggered: {reason}")
        logger.warning("Idle auto-shutdown: %s", reason)
        try:
            await agent_client.shutdown()
        except Exception:
            logger.exception("Idle shutdown request to agent failed")
            event_log.add_event("idle_shutdown", "Idle shutdown request to agent FAILED")

    def llm_idle_seconds(self) -> Optional[float]:
        """Seconds since the last LLM activity (or becoming ready), for display."""
        if self.ready_since is None:
            return None
        reference = max(
            self.ready_since,
            self.last_llm_activity or 0.0,
            self._wake_requested_at or 0.0,
        )
        return time.monotonic() - reference

    def idle_status(self) -> dict[str, Any]:
        report = self._activity_report or {}
        return {
            "enabled": settings.idle_shutdown_enabled,
            "keep_awake": self.keep_awake,
            "active_streams": self.active_llm_streams,
            "strikes": self.idle_strikes,
            "shutdown_after_minutes": settings.idle_shutdown_minutes,
            "llm_idle_seconds": self.llm_idle_seconds(),
            "min_active_idle_seconds": report.get("min_active_idle_seconds"),
            "gpu_util_percent": report.get("gpu_util_percent"),
            "last_idle_shutdown": self.last_idle_shutdown,
        }

    def _models_cache_stale(self) -> bool:
        return (
            self._models_refreshed_at is None
            or time.monotonic() - self._models_refreshed_at > _MODEL_CACHE_TTL
        )

    def _wake_timed_out(self) -> bool:
        return (
            self._wake_requested_at is not None
            and time.monotonic() - self._wake_requested_at > settings.host_reachability_timeout
        )

    async def request_wake(self) -> None:
        async with self._lock:
            self.last_wake_request = self._now()
            self._wake_requested_at = time.monotonic()
            if self.state in (State.host_up, State.ollama_starting, State.ready, State.waking):
                return
            self.state = State.waking

        event_log.add_event("wake_request", "Wake-on-LAN request initiated")
        logger.info("Sending Wake-on-LAN to %s (broadcast %s)", settings.pc_mac, settings.pc_broadcast)
        await wol_svc.send_magic_packet(settings.pc_mac, settings.pc_broadcast)


broker_state = BrokerState()
