"""Broker state machine.

States
------
offline   PC is not reachable.
waking    Wake-on-LAN packet sent; waiting for the PC to respond.
host_up   PC is reachable on the network.
timeout   Wake attempt exceeded the configured timeout.
error     Unexpected error during polling.

The broker tracks only PC reachability. The LLM-runtime states (Ollama
readiness, model availability) were intentionally removed for the power-only
build and can be reintroduced alongside the Ollama service later.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from app import events as event_log
from app.config import settings
from app.services import ping as ping_svc
from app.services import wol as wol_svc

logger = logging.getLogger(__name__)


class State(str, Enum):
    offline = "offline"
    waking = "waking"
    host_up = "host_up"
    timeout = "timeout"
    error = "error"


class BrokerState:
    def __init__(self) -> None:
        self.state: State = State.offline
        self.pc_reachable: bool = False
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
        pc_up = await ping_svc.is_reachable(settings.pc_host, settings.pc_reachability_port)

        async with self._lock:
            self.pc_reachable = pc_up
            if pc_up:
                self.last_seen = self._now()

            previous = self.state
            if pc_up:
                self.state = State.host_up
            elif self.state not in (State.waking, State.timeout, State.error):
                self.state = State.offline

            if previous != self.state:
                event_log.add_event(
                    "state_change",
                    f"State changed from {previous.value} to {self.state.value}",
                )
                logger.info("State: %s -> %s", previous.value, self.state.value)

    async def request_wake(self) -> None:
        async with self._lock:
            self.last_wake_request = self._now()
            if self.state in (State.host_up, State.waking):
                return
            self.state = State.waking

        event_log.add_event("wake_request", "Wake-on-LAN request initiated")
        logger.info("Sending Wake-on-LAN to %s (broadcast %s)", settings.pc_mac, settings.pc_broadcast)
        await wol_svc.send_magic_packet(settings.pc_mac, settings.pc_broadcast)


broker_state = BrokerState()
