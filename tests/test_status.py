"""Tests for /api/status and /healthz and /readyz endpoints."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_status_shape(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "state" in data
    assert "pc" in data
    assert "ollama" in data
    assert "reachable" in data["pc"]
    assert "reachable" in data["ollama"]
    assert "models" in data["ollama"]


async def test_readyz_when_ollama_unreachable(client):
    """Readyz should return 503 when Ollama is not reachable (default in tests)."""
    from app.state import broker_state
    original = broker_state.ollama_reachable
    broker_state.ollama_reachable = False
    try:
        resp = await client.get("/readyz")
        assert resp.status_code == 503
    finally:
        broker_state.ollama_reachable = original


async def test_readyz_when_ollama_reachable(client):
    from app.state import broker_state
    original = broker_state.ollama_reachable
    broker_state.ollama_reachable = True
    try:
        resp = await client.get("/readyz")
        assert resp.status_code == 200
    finally:
        broker_state.ollama_reachable = original
