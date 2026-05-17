"""Tests for /api/llm/* endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

pytestmark = pytest.mark.asyncio


async def test_llm_health_unavailable(client):
    """Should return 503 when Ollama is unreachable."""
    with patch("app.services.ollama.health_and_models", new_callable=AsyncMock) as mock:
        mock.return_value = (False, [])
        resp = await client.get("/api/llm/health")
    assert resp.status_code == 503


async def test_llm_health_ok(client):
    with patch("app.services.ollama.health_and_models", new_callable=AsyncMock) as mock:
        mock.return_value = (True, ["llama3"])
        resp = await client.get("/api/llm/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_llm_models_unavailable(client):
    with patch("app.services.ollama.health_and_models", new_callable=AsyncMock) as mock:
        mock.return_value = (False, [])
        resp = await client.get("/api/llm/models")
    assert resp.status_code == 503


async def test_llm_models_ok(client):
    with patch("app.services.ollama.health_and_models", new_callable=AsyncMock) as mock:
        mock.return_value = (True, ["llama3", "deepseek-r1"])
        resp = await client.get("/api/llm/models")
    assert resp.status_code == 200
    assert "models" in resp.json()
    assert len(resp.json()["models"]) == 2


async def test_llm_chat_when_ready(client):
    from app.state import broker_state, State
    original_state = broker_state.state
    original_reachable = broker_state.ollama_reachable
    broker_state.state = State.ready
    broker_state.ollama_reachable = True

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.content = b'{"message":{"role":"assistant","content":"Hello!"}}'
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}

    try:
        with patch("app.services.ollama.proxy_chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = mock_response
            resp = await client.post(
                "/api/llm/chat",
                json={"model": "llama3", "messages": [{"role": "user", "content": "Hi"}]},
            )
        assert resp.status_code == 200
    finally:
        broker_state.state = original_state
        broker_state.ollama_reachable = original_reachable
