"""Tests for /api/status and /healthz and /readyz endpoints."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz(client):
    """Readyz is ready as soon as the process is up (no PC dependency)."""
    resp = await client.get("/readyz")
    assert resp.status_code == 200


async def test_status_shape(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "state" in data
    assert "pc" in data
    assert "reachable" in data["pc"]
    assert "last_seen" in data["pc"]
    assert "last_wake_request" in data
    assert "ollama" in data
    assert "reachable" in data["ollama"]
    assert isinstance(data["ollama"]["models"], list)
