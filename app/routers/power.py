"""POST /api/power/on and POST /api/power/off"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException

from app.config import settings
from app import events as event_log
from app.state import broker_state

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/power/on", status_code=202)
async def power_on():
    """Send Wake-on-LAN packet to the gaming PC."""
    if not settings.pc_mac:
        raise HTTPException(status_code=503, detail="PC_MAC not configured")
    await broker_state.request_wake()
    return {"accepted": True, "message": "Wake-on-LAN packet sent"}


@router.post("/api/power/off", status_code=202)
async def power_off():
    """Request graceful shutdown via the optional shutdown agent."""
    if not settings.shutdown_agent_url:
        raise HTTPException(
            status_code=503,
            detail="Shutdown agent not configured (SHUTDOWN_AGENT_URL is not set)",
        )
    headers = {}
    if settings.shutdown_agent_token:
        headers["Authorization"] = f"Bearer {settings.shutdown_agent_token}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{settings.shutdown_agent_url}/shutdown", headers=headers)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Shutdown agent returned {exc.response.status_code}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Shutdown agent unreachable: {exc}") from exc

    event_log.add_event("shutdown_request", "Graceful shutdown requested via agent")
    logger.info("Shutdown request sent to agent at %s", settings.shutdown_agent_url)
    return {"accepted": True, "message": "Shutdown request sent"}
