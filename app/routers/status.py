"""GET /api/status"""
from __future__ import annotations

from fastapi import APIRouter

from app.state import broker_state

router = APIRouter()


@router.get("/api/status")
async def get_status():
    return {
        "state": broker_state.state.value,
        "pc": {
            "reachable": broker_state.pc_reachable,
            "last_seen": broker_state.last_seen,
        },
        "last_wake_request": broker_state.last_wake_request,
    }
