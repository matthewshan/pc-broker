"""Broker state machine.

States
------
offline         PC is not reachable; Ollama is not reachable.
waking          Wake-on-LAN packet sent; waiting for PC to respond.
host_up         PC is reachable; waiting for Ollama to start.
ollama_starting Ollama port is open; models may still be loading.
ready           Ollama is healthy and models are available.
timeout         Wake / readiness attempt exceeded configured timeout.
error           Unexpected error during polling.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from app import events as event_log
from app.config import settings
from app.services import ollama as ollama_svc
from app.services import ping as ping_svc
from app.services import wol as wol_svc

logger = logging.getLogger(__name__)


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
        self.ollama_reachable: bool = False
        self.models: list[str] = []
        self.last_seen: Optional[str] = None
        self.last_wake_request: Optional[str] = None
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
        pc_up = await ping_svc.is_reachable(settings.pc_host)
        ollama_up, models = await ollama_svc.health_and_models(settings.ollama_base_url)

        async with self._lock:
            self.pc_reachable = pc_up
            self.ollama_reachable = ollama_up
            if pc_up:
                self.last_seen = self._now()
            if ollama_up:
                self.models = models

            previous = self.state
            if ollama_up:
                self.state = State.ready
            elif pc_up:
                self.state = State.host_up
            elif self.state not in (State.waking, State.timeout, State.error):
                self.state = State.offline

            if previous != self.state:
                event_log.add_event(
                    "state_change",
                    f"State changed from {previous.value} to {self.state.value}",
                )
                logger.info("State: %s → %s", previous.value, self.state.value)

    async def request_wake(self) -> None:
        async with self._lock:
            self.last_wake_request = self._now()
            if self.state in (State.ready, State.waking):
                return
            self.state = State.waking

        event_log.add_event("wake_request", "Wake-on-LAN request initiated")
        logger.info("Sending Wake-on-LAN to %s (broadcast %s)", settings.pc_mac, settings.pc_broadcast)
        await wol_svc.send_magic_packet(settings.pc_mac, settings.pc_broadcast)

    async def wait_until_ready(self) -> bool:
        """Poll until Ollama is ready or timeout is exceeded. Returns True if ready."""
        deadline = asyncio.get_event_loop().time() + settings.ollama_readiness_timeout
        host_deadline = asyncio.get_event_loop().time() + settings.host_reachability_timeout

        while asyncio.get_event_loop().time() < deadline:
            await self._refresh()
            async with self._lock:
                if self.state == State.ready:
                    return True
                if self.state == State.error:
                    return False
                # Upgrade state labels while we wait
                if self.pc_reachable and self.state == State.waking:
                    self.state = State.host_up
                    event_log.add_event("host_up", "PC is now reachable")
                if self.ollama_reachable and self.state in (State.host_up, State.waking):
                    self.state = State.ollama_starting
                    event_log.add_event("ollama_starting", "Ollama port is open")

            if asyncio.get_event_loop().time() > host_deadline and not self.pc_reachable:
                async with self._lock:
                    self.state = State.timeout
                event_log.add_event("timeout", "Timed out waiting for PC to become reachable")
                logger.warning("Timed out waiting for PC reachability")
                return False

            await asyncio.sleep(settings.poll_interval)

        async with self._lock:
            self.state = State.timeout
        event_log.add_event("timeout", "Timed out waiting for Ollama to become ready")
        logger.warning("Timed out waiting for Ollama readiness")
        return False


broker_state = BrokerState()
