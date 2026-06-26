"""Tests for /api/power/on and /api/power/off."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_power_on_no_mac(client):
    """Power on should return 503 when PC_MAC is not configured."""
    from app.config import settings
    original = settings.pc_mac
    settings.pc_mac = ""
    try:
        resp = await client.post("/api/power/on")
        assert resp.status_code == 503
    finally:
        settings.pc_mac = original


async def test_power_on_sends_wol(client):
    """Power on should send WoL and return 202."""
    from app.config import settings
    original = settings.pc_mac
    settings.pc_mac = "aa:bb:cc:dd:ee:ff"
    try:
        with patch("app.services.wol.send_magic_packet", new_callable=AsyncMock):
            resp = await client.post("/api/power/on")
        assert resp.status_code == 202
        data = resp.json()
        assert data["accepted"] is True
    finally:
        settings.pc_mac = original


async def test_power_off_no_agent(client):
    """Power off should return 503 when shutdown agent is not configured."""
    from app.config import settings
    original = settings.shutdown_agent_url
    settings.shutdown_agent_url = ""
    try:
        resp = await client.post("/api/power/off")
        assert resp.status_code == 503
    finally:
        settings.shutdown_agent_url = original
