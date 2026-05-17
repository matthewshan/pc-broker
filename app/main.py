"""FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers import events, llm, power, status, ui
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
        title="LLM PC Broker",
        description="Internal service to wake a gaming PC, check LLM readiness, and proxy Ollama requests.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Health probes
    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        if broker_state.ollama_reachable:
            return {"status": "ok"}
        return JSONResponse(status_code=503, content={"status": "not_ready", "state": broker_state.state.value})

    # Static files
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # Routers
    app.include_router(status.router, tags=["status"])
    app.include_router(power.router, tags=["power"])
    app.include_router(llm.router, tags=["llm"])
    app.include_router(events.router, tags=["events"])
    app.include_router(ui.router, include_in_schema=False)

    return app


app = create_app()
