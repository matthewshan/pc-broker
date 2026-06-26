"""POST /api/power/on and POST /api/power/off"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Header, HTTPException

from app.config import settings
from app import events as event_log
from app.state import broker_state

logger = logging.getLogger(__name__)
router = APIRouter()


def _check_api_token(authorization: str | None) -> None:
    """Enforce the shared API token when one is configured.

    Access is normally gated at the network layer (Twingate). When ``API_TOKEN``
    is set, the sensitive shutdown path additionally requires a matching
    ``Authorization: Bearer <token>`` header.
    """
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


@router.post("/api/power/on", status_code=202)
async def power_on():
    """Send Wake-on-LAN packet to the gaming PC."""
    if not settings.pc_mac:
        raise HTTPException(status_code=503, detail="PC_MAC not configured")
    await broker_state.request_wake()
    return {"accepted": True, "message": "Wake-on-LAN packet sent"}


@router.post("/api/power/off", status_code=202)
async def power_off(authorization: str | None = Header(default=None)):
    """Request graceful shutdown via the shutdown agent on the PC."""
    _check_api_token(authorization)
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
