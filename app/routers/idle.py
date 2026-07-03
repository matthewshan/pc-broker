"""POST /api/idle/keep_awake - pause/resume idle auto-shutdown."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app import events as event_log
from app.state import broker_state

logger = logging.getLogger(__name__)
router = APIRouter()


class KeepAwakeRequest(BaseModel):
    enabled: bool


@router.post("/api/idle/keep_awake")
async def set_keep_awake(req: KeepAwakeRequest):
    broker_state.keep_awake = req.enabled
    broker_state.idle_strikes = 0
    verb = "enabled" if req.enabled else "disabled"
    event_log.add_event("keep_awake", f"Keep-awake {verb}")
    logger.info("Keep-awake %s", verb)
    return {"keep_awake": broker_state.keep_awake}
