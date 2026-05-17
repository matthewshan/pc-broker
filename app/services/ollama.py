"""Ollama health check and proxy helpers."""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


async def health_and_models(base_url: str) -> tuple[bool, list[str]]:
    """Return (reachable, model_names) by querying the Ollama /api/tags endpoint."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return True, models
    except Exception as exc:
        logger.debug("Ollama health check failed: %s", exc)
        return False, []


async def proxy_chat(base_url: str, payload: dict) -> httpx.Response:
    """Forward a chat request to Ollama and return the raw response."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        return await client.post(f"{base_url}/api/chat", json=payload)


async def proxy_generate(base_url: str, payload: dict) -> httpx.Response:
    """Forward a generate request to Ollama and return the raw response."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        return await client.post(f"{base_url}/api/generate", json=payload)


async def proxy_embeddings(base_url: str, payload: dict) -> httpx.Response:
    """Forward an embeddings request to Ollama."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        return await client.post(f"{base_url}/api/embeddings", json=payload)
