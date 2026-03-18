"""Tests for the Web UI endpoints and WebSocket handler."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


@pytest_asyncio.fixture
async def client():
    """Create test aiohttp client with web UI routes."""
    mock_ollama = MagicMock()
    mock_ollama.model = "llama3.1:8b"
    mock_ollama.health_check = AsyncMock(return_value=True)

    mock_graph = MagicMock()
    mock_graph.is_configured = True
    mock_graph.is_user_connected = MagicMock(return_value=False)

    mock_nexgai = MagicMock()
    mock_nexgai.is_configured = False
    mock_nexgai.is_available = False

    mock_agent = MagicMock()
    mock_agent.process_message = AsyncMock(return_value={
        "text": "Hello from MyAi!",
        "message_id": 42,
        "conversation_id": "conv-test",
        "source": "local",
        "agent_name": None,
    })

    mock_bot = MagicMock()
    mock_bot._handle_command = AsyncMock(return_value=None)

    mock_feedback = MagicMock()
    mock_feedback.submit = AsyncMock(return_value="fb-123")

    with patch("app.main.ollama_client", mock_ollama), \
         patch("app.main.graph_client", mock_graph), \
         patch("app.main.nexgai_client", mock_nexgai), \
         patch("app.main.agent", mock_agent), \
         patch("app.main.bot", mock_bot), \
         patch("app.main.feedback_service", mock_feedback):

        from app.main import create_debug_app
        app = create_debug_app()
        async with TestClient(TestServer(app)) as tc:
            # Attach mocks for test assertions
            tc._mock_agent = mock_agent
            tc._mock_bot = mock_bot
            yield tc


# -- Health Endpoint Tests --

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert data["model"] == "llama3.1:8b"


# -- Web API Tests --

class TestWebAPI:
    @pytest.mark.asyncio
    async def test_web_status(self, client):
        resp = await client.get("/api/web/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["ollama"] is True
        assert data["model"] == "llama3.1:8b"
        assert data["graph"] == "configured"

    @pytest.mark.asyncio
    async def test_web_skills(self, client):
        resp = await client.get("/api/web/skills")
        assert resp.status == 200
        data = await resp.json()
        # With NexgAI not configured, only the general MyAi LLM entry
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "general"
        assert data["skills"][0]["agent"] == "MyAi"
        assert data["skills"][0]["source"] == "local"


# -- Static Files Tests --

class TestStaticFiles:
    @pytest.mark.asyncio
    async def test_index_page(self, client):
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "MyAi" in text
        assert "<!DOCTYPE html>" in text

    @pytest.mark.asyncio
    async def test_css_served(self, client):
        resp = await client.get("/static/styles.css")
        assert resp.status == 200
        text = await resp.text()
        assert "--bg-primary" in text

    @pytest.mark.asyncio
    async def test_js_served(self, client):
        resp = await client.get("/static/app.js")
        assert resp.status == 200
        text = await resp.text()
        assert "WebSocket" in text


# -- WebSocket Tests --

class TestWebSocket:
    @pytest.mark.asyncio
    async def test_ws_connect_and_auth(self, client):
        async with client.ws_connect("/ws") as ws:
            await ws.send_json({
                "type": "auth",
                "user_id": "test-user",
                "user_name": "Tester",
            })
            resp = await ws.receive_json()
            assert resp["type"] == "system"
            assert "Connected" in resp["text"]

    @pytest.mark.asyncio
    async def test_ws_send_message(self, client):
        async with client.ws_connect("/ws") as ws:
            await ws.send_json({
                "type": "auth",
                "user_id": "test-user",
                "user_name": "Tester",
            })
            await ws.receive_json()  # system message

            await ws.send_json({
                "type": "message",
                "text": "Hello MyAi",
                "user_id": "test-user",
                "user_name": "Tester",
            })

            typing = await ws.receive_json()
            assert typing["type"] == "typing"

            resp = await ws.receive_json()
            assert resp["type"] == "response"
            assert resp["text"] == "Hello from MyAi!"

    @pytest.mark.asyncio
    async def test_ws_command_falls_through_to_agent(self, client):
        """When bot._handle_command returns None, falls through to agent."""
        async with client.ws_connect("/ws") as ws:
            await ws.send_json({
                "type": "auth",
                "user_id": "test-user",
                "user_name": "Tester",
            })
            await ws.receive_json()

            await ws.send_json({
                "type": "message",
                "text": "/unknown-command",
                "user_id": "test-user",
                "user_name": "Tester",
            })

            typing = await ws.receive_json()
            assert typing["type"] == "typing"

            resp = await ws.receive_json()
            assert resp["type"] == "response"

    @pytest.mark.asyncio
    async def test_ws_command_handled(self, client):
        """When bot._handle_command returns a string, it's sent as response."""
        client._mock_bot._handle_command = AsyncMock(return_value="*Help text here*")

        async with client.ws_connect("/ws") as ws:
            await ws.send_json({
                "type": "auth",
                "user_id": "test-user",
                "user_name": "Tester",
            })
            await ws.receive_json()

            await ws.send_json({
                "type": "message",
                "text": "/help",
                "user_id": "test-user",
                "user_name": "Tester",
            })

            typing = await ws.receive_json()
            assert typing["type"] == "typing"

            resp = await ws.receive_json()
            assert resp["type"] == "response"
            assert "Help text" in resp["text"]

    @pytest.mark.asyncio
    async def test_ws_invalid_json(self, client):
        async with client.ws_connect("/ws") as ws:
            await ws.send_str("not valid json")
            resp = await ws.receive_json()
            assert resp["type"] == "error"
            assert "Invalid JSON" in resp["text"]

    @pytest.mark.asyncio
    async def test_ws_agent_error_handled(self, client):
        client._mock_agent.process_message = AsyncMock(
            side_effect=Exception("Ollama unreachable")
        )

        async with client.ws_connect("/ws") as ws:
            await ws.send_json({
                "type": "auth",
                "user_id": "test-user",
                "user_name": "Tester",
            })
            await ws.receive_json()

            await ws.send_json({
                "type": "message",
                "text": "Hello",
                "user_id": "test-user",
                "user_name": "Tester",
            })

            typing = await ws.receive_json()
            resp = await ws.receive_json()
            assert resp["type"] == "error"
            assert "Ollama unreachable" in resp["text"]

    @pytest.mark.asyncio
    async def test_ws_empty_message_ignored(self, client):
        async with client.ws_connect("/ws") as ws:
            await ws.send_json({
                "type": "auth",
                "user_id": "test-user",
                "user_name": "Tester",
            })
            await ws.receive_json()

            # Send empty message — should be silently ignored
            await ws.send_json({
                "type": "message",
                "text": "",
                "user_id": "test-user",
            })

            # Verify connection still works
            await ws.send_json({
                "type": "message",
                "text": "test",
                "user_id": "test-user",
                "user_name": "Tester",
            })
            typing = await ws.receive_json()
            assert typing["type"] == "typing"
