"""FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import events, idle, llm, power, status, ui
from app.state import broker_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await broker_state.start_background_poll()
    yield
    await broker_state.stop_background_poll()


def create_app() -> FastAPI:
    app = FastAPI(
        title="PC Broker",
        description="Internal service to wake and shut down a gaming PC over the LAN.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Health probes. The broker has no external runtime dependency, so it is
    # ready as soon as the process is up; readiness must not depend on the PC
    # being reachable (the PC is expected to be off most of the time).
    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        return {"status": "ok"}

    # Static files
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # Routers
    app.include_router(status.router, tags=["status"])
    app.include_router(power.router, tags=["power"])
    app.include_router(events.router, tags=["events"])
    app.include_router(llm.router, tags=["llm"])
    app.include_router(idle.router, tags=["idle"])
    app.include_router(ui.router, include_in_schema=False)

    return app


app = create_app()
