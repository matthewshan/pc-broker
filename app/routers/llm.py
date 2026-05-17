"""LLM proxy endpoints: /api/llm/health, /api/llm/models, /api/llm/chat, etc."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.config import settings
from app import events as event_log
from app.services import ollama as ollama_svc
from app.state import State, broker_state

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    options: Optional[dict[str, Any]] = None


class GenerateRequest(BaseModel):
    model: str
    prompt: str
    stream: bool = False
    options: Optional[dict[str, Any]] = None


class EmbeddingsRequest(BaseModel):
    model: str
    prompt: str


async def _ensure_ready() -> None:
    """Wake the PC and wait for Ollama to be ready. Raises 503 on timeout."""
    if broker_state.state == State.ready:
        return
    await broker_state.request_wake()
    ready = await broker_state.wait_until_ready()
    if not ready:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "warming_up",
                "state": broker_state.state.value,
                "message": "PC or Ollama not ready. Try again shortly.",
            },
        )


@router.get("/api/llm/health")
async def llm_health():
    reachable, _ = await ollama_svc.health_and_models(settings.ollama_base_url)
    if reachable:
        return {"status": "ok"}
    raise HTTPException(status_code=503, detail={"status": "unavailable", "state": broker_state.state.value})


@router.get("/api/llm/models")
async def llm_models():
    reachable, models = await ollama_svc.health_and_models(settings.ollama_base_url)
    if not reachable:
        raise HTTPException(status_code=503, detail="Ollama not reachable")
    return {"models": models}


@router.post("/api/llm/chat")
async def llm_chat(req: ChatRequest):
    await _ensure_ready()
    event_log.add_event("proxy_request", f"Chat request for model {req.model!r}")
    try:
        resp = await ollama_svc.proxy_chat(settings.ollama_base_url, req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type", "application/json"))


@router.post("/api/llm/generate")
async def llm_generate(req: GenerateRequest):
    await _ensure_ready()
    event_log.add_event("proxy_request", f"Generate request for model {req.model!r}")
    try:
        resp = await ollama_svc.proxy_generate(settings.ollama_base_url, req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type", "application/json"))


@router.post("/api/llm/embeddings")
async def llm_embeddings(req: EmbeddingsRequest):
    await _ensure_ready()
    try:
        resp = await ollama_svc.proxy_embeddings(settings.ollama_base_url, req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type", "application/json"))
