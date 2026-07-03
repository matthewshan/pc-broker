"""Ollama client helpers (health probe, model listing, streaming chat proxy)."""
from __future__ import annotations

from typing import Any, AsyncIterator

import httpx

from app.config import settings


async def is_healthy() -> bool:
    """Return True if Ollama answers its version endpoint within the timeout."""
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_health_timeout) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/version")
            return resp.status_code == 200
    except Exception:
        return False


async def list_models() -> list[dict[str, Any]]:
    """Return the models Ollama has available. Raises on failure."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{settings.ollama_base_url}/api/tags")
        resp.raise_for_status()
        return [
            {
                "name": m.get("name"),
                "size": m.get("size"),
                "modified_at": m.get("modified_at"),
            }
            for m in resp.json().get("models", [])
        ]


async def chat_stream(payload: dict[str, Any]) -> AsyncIterator[bytes]:
    """Proxy a chat request to Ollama, yielding raw NDJSON chunks.

    read=None: a cold model load can take 30-90s before the first token, and
    thinking-model token gaps can be long. The client (and stream) are opened
    inside the generator so a downstream disconnect unwinds the context
    managers and aborts the upstream Ollama generation.
    """
    timeout = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST", f"{settings.ollama_base_url}/api/chat", json=payload
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                yield chunk
