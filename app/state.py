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
from app.services import ollama as ollama_svc
from app.services import ping as ping_svc
from app.services import wol as wol_svc

logger = logging.getLogger(__name__)

_MODEL_CACHE_TTL = 60.0


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
        self._lock = asyncio.Lock()
        self._poll_task: Optional[asyncio.Task] = None

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

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
                event_log.add_event(
                    "state_change",
                    f"State changed from {previous.value} to {self.state.value}",
                )
                logger.info("State: %s -> %s", previous.value, self.state.value)

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
