"""HTTP client for the shutdown agent on the gaming PC."""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    if settings.shutdown_agent_token:
        return {"Authorization": f"Bearer {settings.shutdown_agent_token}"}
    return {}


async def shutdown() -> None:
    """Request a graceful shutdown. Raises httpx errors on failure."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{settings.shutdown_agent_url}/shutdown", headers=_headers())
        resp.raise_for_status()


async def get_activity() -> Optional[dict[str, Any]]:
    """Fetch the agent's user-activity report; None on any failure.

    None must be treated as "user is active" by the idle-shutdown logic.
    """
    if not settings.shutdown_agent_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.shutdown_agent_url}/activity", headers=_headers())
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning("Agent /activity fetch failed", exc_info=True)
        return None
