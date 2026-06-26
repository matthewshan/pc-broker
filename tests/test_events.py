"""Tests for /api/events."""
from __future__ import annotations

import pytest
from app import events as event_log

pytestmark = pytest.mark.asyncio


async def test_events_empty_by_default(client):
    # Clear events first
    event_log._events.clear()
    resp = await client.get("/api/events")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_events_returns_added_event(client):
    event_log._events.clear()
    event_log.add_event("test_kind", "test message")
    resp = await client.get("/api/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["kind"] == "test_kind"
    assert data[0]["message"] == "test message"
    assert "timestamp" in data[0]


async def test_events_limit(client):
    event_log._events.clear()
    for i in range(10):
        event_log.add_event("kind", f"msg {i}")
    resp = await client.get("/api/events?limit=3")
    assert resp.status_code == 200
    assert len(resp.json()) == 3
