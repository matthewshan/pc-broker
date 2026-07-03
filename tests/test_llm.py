"""Tests for /api/llm/* endpoints."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.state import State, broker_state

pytestmark = pytest.mark.asyncio


async def test_chat_not_ready(client):
    """Chat should 503 with the current state when the PC/Ollama is not ready."""
    original = broker_state.state
    broker_state.state = State.offline
    try:
        resp = await client.post(
            "/api/llm/chat",
            json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 503
        assert resp.json()["detail"]["state"] == "offline"
    finally:
        broker_state.state = original


async def test_models_not_ready(client):
    original = broker_state.state
    broker_state.state = State.waking
    try:
        resp = await client.get("/api/llm/models")
        assert resp.status_code == 503
    finally:
        broker_state.state = original


async def test_models_ready(client):
    original = broker_state.state
    broker_state.state = State.ready
    models = [{"name": "qwen3:8b", "size": 1, "modified_at": "x"}]
    try:
        with patch("app.services.ollama.list_models", new=AsyncMock(return_value=models)):
            resp = await client.get("/api/llm/models")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["name"] == "qwen3:8b"
        assert broker_state.models == models
    finally:
        broker_state.state = original


async def test_chat_streams_ndjson(client):
    original = broker_state.state
    broker_state.state = State.ready
    lines = [
        {"message": {"role": "assistant", "content": "Hel"}, "done": False},
        {"message": {"role": "assistant", "content": "lo"}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True},
    ]

    async def fake_stream(payload):
        assert payload["stream"] is True
        assert payload["model"] == "qwen3:8b"
        for line in lines:
            yield (json.dumps(line) + "\n").encode()

    try:
        with patch("app.services.ollama.chat_stream", new=fake_stream):
            resp = await client.post(
                "/api/llm/chat",
                json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        received = [json.loads(l) for l in resp.text.strip().split("\n")]
        assert received == lines
        assert broker_state.last_llm_activity is not None
        assert broker_state.active_llm_streams == 0
    finally:
        broker_state.state = original


async def test_chat_upstream_unreachable(client):
    original = broker_state.state
    broker_state.state = State.ready

    async def failing_stream(payload):
        raise ConnectionError("boom")
        yield  # pragma: no cover - makes this an async generator

    try:
        with patch("app.services.ollama.chat_stream", new=failing_stream):
            resp = await client.post(
                "/api/llm/chat",
                json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 503
        assert broker_state.active_llm_streams == 0
    finally:
        broker_state.state = original
