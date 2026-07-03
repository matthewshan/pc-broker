"""Tests for the broker state machine transitions."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.state import BrokerState, State

pytestmark = pytest.mark.asyncio


def _fresh_state() -> BrokerState:
    return BrokerState()


async def _refresh(state: BrokerState, *, pc_up: bool, ollama_up: bool, models=None):
    with patch("app.services.ping.is_reachable", new=AsyncMock(return_value=pc_up)), \
         patch("app.services.ollama.is_healthy", new=AsyncMock(return_value=ollama_up)), \
         patch("app.services.ollama.list_models", new=AsyncMock(return_value=models or [])):
        await state._refresh()


async def test_offline_to_ollama_starting():
    state = _fresh_state()
    await _refresh(state, pc_up=True, ollama_up=False)
    assert state.state is State.ollama_starting
    assert state.pc_reachable is True
    assert state.ollama_reachable is False


async def test_ollama_starting_to_ready_with_models():
    state = _fresh_state()
    await _refresh(state, pc_up=True, ollama_up=False)
    await _refresh(state, pc_up=True, ollama_up=True, models=[{"name": "qwen3:8b"}])
    assert state.state is State.ready
    assert state.ollama_reachable is True
    assert state.models == [{"name": "qwen3:8b"}]


async def test_ready_to_offline_clears_ollama():
    state = _fresh_state()
    await _refresh(state, pc_up=True, ollama_up=True, models=[{"name": "qwen3:8b"}])
    await _refresh(state, pc_up=False, ollama_up=False)
    assert state.state is State.offline
    assert state.ollama_reachable is False
    assert state.models == []


async def test_waking_holds_until_timeout():
    state = _fresh_state()
    with patch("app.services.wol.send_magic_packet", new_callable=AsyncMock):
        await state.request_wake()
    assert state.state is State.waking

    # Still waking while within the timeout window.
    await _refresh(state, pc_up=False, ollama_up=False)
    assert state.state is State.waking

    # Push the wake request past the deadline.
    from app.config import settings
    state._wake_requested_at -= settings.host_reachability_timeout + 1
    await _refresh(state, pc_up=False, ollama_up=False)
    assert state.state is State.timeout


async def test_timeout_recovers_when_pc_comes_up():
    state = _fresh_state()
    state.state = State.timeout
    await _refresh(state, pc_up=True, ollama_up=True, models=[{"name": "qwen3:8b"}])
    assert state.state is State.ready
