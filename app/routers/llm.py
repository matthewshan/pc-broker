"""LLM endpoints: /api/llm/health, /api/llm/models, /api/llm/chat.

The chat endpoint is a streaming NDJSON passthrough to Ollama on the gaming
PC. It refuses with 503 unless the broker state is `ready`; the UI uses that
to show the wake prompt instead of a chat error.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import events as event_log
from app.services import ollama as ollama_svc
from app.state import State, broker_state

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    options: Optional[dict[str, Any]] = None


# Ollama honors resource-shaping options per request (num_ctx, num_gpu, ...);
# an oversized num_ctx can exhaust the PC's VRAM/RAM. Only pass through known
# sampling options, with hard caps on the context/prediction sizes.
_ALLOWED_OPTIONS = {
    "temperature", "top_p", "top_k", "min_p", "seed", "stop",
    "repeat_penalty", "presence_penalty", "frequency_penalty",
    "num_ctx", "num_predict",
}
_OPTION_CAPS = {"num_ctx": 16384, "num_predict": 8192}


def _sanitize_options(options: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in options.items():
        if key not in _ALLOWED_OPTIONS:
            continue
        cap = _OPTION_CAPS.get(key)
        if cap is not None:
            try:
                value = min(int(value), cap)
            except (TypeError, ValueError):
                continue
        clean[key] = value
    return clean


def _require_ready() -> None:
    if broker_state.state is not State.ready:
        raise HTTPException(
            status_code=503,
            detail={
                "detail": "PC/Ollama not ready",
                "state": broker_state.state.value,
            },
        )


@router.get("/api/llm/health")
async def llm_health():
    reachable = await ollama_svc.is_healthy()
    return {"reachable": reachable, "state": broker_state.state.value}


@router.get("/api/llm/models")
async def llm_models():
    _require_ready()
    try:
        models = await ollama_svc.list_models()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama model listing failed: {exc}") from exc
    broker_state.models = models
    return {"models": models}


async def _stream_with_bookkeeping(payload: dict[str, Any]) -> AsyncIterator[bytes]:
    broker_state.active_llm_streams += 1
    broker_state.note_llm_activity()
    try:
        async for chunk in ollama_svc.chat_stream(payload):
            broker_state.note_llm_activity()
            yield chunk
    finally:
        broker_state.active_llm_streams -= 1
        broker_state.note_llm_activity()


@router.post("/api/llm/chat")
async def llm_chat(req: ChatRequest):
    _require_ready()
    payload = req.model_dump(exclude_none=True)
    if "options" in payload:
        payload["options"] = _sanitize_options(payload["options"])
    payload["stream"] = True

    # Surface connection/model errors as clean HTTP errors before committing
    # to a streamed response; after the first byte, errors can only truncate
    # the stream (headers are already sent).
    stream = _stream_with_bookkeeping(payload)
    try:
        first_chunk = await anext(stream)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama returned {exc.response.status_code}",
        ) from exc
    except StopAsyncIteration:
        first_chunk = b""
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Ollama unreachable: {exc}") from exc

    event_log.add_event("proxy_request", f"Chat request proxied to model {req.model}")

    async def replay() -> AsyncIterator[bytes]:
        if first_chunk:
            yield first_chunk
        async for chunk in stream:
            yield chunk

    return StreamingResponse(replay(), media_type="application/x-ndjson")
