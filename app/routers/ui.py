"""UI routes - serve the built-in status + power dashboard."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.state import broker_state
from app import events as event_log

router = APIRouter()

templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    events = event_log.get_events(limit=20)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "state": broker_state.state.value,
            "pc": {
                "reachable": broker_state.pc_reachable,
                "last_seen": broker_state.last_seen,
                "host": settings.pc_host,
            },
            "last_wake_request": broker_state.last_wake_request,
            "events": events,
            "version": settings.broker_version,
            "host_reachability_timeout": settings.host_reachability_timeout,
            "poll_interval": settings.poll_interval,
        },
    )
