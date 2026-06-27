"""GET /api/events"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app import events as event_log

router = APIRouter()


@router.get("/api/events")
async def list_events(limit: int = Query(default=50, ge=1, le=200)):
    return event_log.get_events(limit=limit)
