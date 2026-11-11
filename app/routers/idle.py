"""POST /api/idle/keep_awake - pause/resume idle auto-shutdown."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import events as event_log
from app.config import settings
from app.state import broker_state

logger = logging.getLogger(__name__)
router = APIRouter()


class KeepAwakeRequest(BaseModel):
    enabled: bool
    # Optional minutes to stay awake. Omitted -> keep_awake_default_minutes.
    # Any value is clamped to keep_awake_max_minutes in the broker, so this is
    # a bounded pause, never the old unbounded latch (bug-053).
    ttl_minutes: Optional[float] = Field(default=None, gt=0)


@router.post("/api/idle/keep_awake")
async def set_keep_awake(req: KeepAwakeRequest):
    broker_state.set_keep_awake(req.enabled, req.ttl_minutes)
    remaining = broker_state.keep_awake_remaining_seconds()
    if req.enabled:
        minutes = round(remaining / 60) if remaining is not None else 0
        event_log.add_event("keep_awake", f"Keep-awake enabled for ~{minutes} min")
        logger.info(
            "Keep-awake enabled for ~%s min (cap %s min)",
            minutes,
            settings.keep_awake_max_minutes,
        )
    else:
        event_log.add_event("keep_awake", "Keep-awake disabled")
        logger.info("Keep-awake disabled")
    return {
        "keep_awake": broker_state.keep_awake_active(),
        "keep_awake_remaining_seconds": remaining,
    }
