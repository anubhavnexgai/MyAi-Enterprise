"""Tests for the NexgAI integration client and routing."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.nexgai_client import CircuitBreaker, NexgAIAgent, NexgAIClient


# ── CircuitBreaker Tests ──


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(threshold=3, cooldown_seconds=60)
        assert not cb.is_open

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(threshold=3, cooldown_seconds=60)
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_open
        cb.record_failure()
        assert cb.is_open

    def test_success_resets_failures(self):
        cb = CircuitBreaker(threshold=3, cooldown_seconds=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        assert not cb.is_open

    def test_reopens_after_cooldown(self):
        cb = CircuitBreaker(threshold=2, cooldown_seconds=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open
        # Simulate cooldown elapsed
        cb._opened_at = time.monotonic() - 2
        assert not cb.is_open

    def test_stays_open_during_cooldown(self):
        cb = CircuitBreaker(threshold=1, cooldown_seconds=9999)
        cb.record_failure()
        assert cb.is_open


# ── NexgAIClient Tests ──


class TestNexgAIClientConfig:
    @patch("app.services.nexgai_client.settings")
    def test_is_configured_when_all_set(self, mock_settings):
        mock_settings.nexgai_enabled = True
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = "svc@test.com"
        mock_settings.nexgai_service_password = "pass"
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 3
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        assert client.is_configured is True

    @patch("app.services.nexgai_client.settings")
    def test_not_configured_when_disabled(self, mock_settings):
        mock_settings.nexgai_enabled = False
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = "svc@test.com"
        mock_settings.nexgai_service_password = ""
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 3
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        assert client.is_configured is False

    @patch("app.services.nexgai_client.settings")
    def test_not_configured_when_no_user(self, mock_settings):
        mock_settings.nexgai_enabled = True
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = ""
        mock_settings.nexgai_service_password = ""
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 3
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        assert client.is_configured is False

    @patch("app.services.nexgai_client.settings")
    def test_is_available_depends_on_circuit_breaker(self, mock_settings):
        mock_settings.nexgai_enabled = True
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = "svc@test.com"
        mock_settings.nexgai_service_password = "pass"
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 2
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        assert client.is_available is True
        # Trip the circuit breaker
        client.circuit_breaker.record_failure()
        client.circuit_breaker.record_failure()
        assert client.is_available is False


@pytest.mark.asyncio
class TestNexgAIClientSendMessage:
    @patch("app.services.nexgai_client.settings")
    async def test_send_message_success(self, mock_settings):
        mock_settings.nexgai_enabled = True
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = "svc@test.com"
        mock_settings.nexgai_service_password = "pass"
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 3
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        client._access_token = "test-token"
        client._token_expires_at = time.monotonic() + 3600

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "message": "Hello from NexgAI",
            "handled_by": "CustomerAgent",
            "session_id": "sess-123",
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await client.send_message(
                message="Hello",
                session_id="sess-123",
                user_id="user-1",
            )

        assert result is not None
        assert result["success"] is True
        assert result["message"] == "Hello from NexgAI"
        assert result["handled_by"] == "CustomerAgent"

    @patch("app.services.nexgai_client.settings")
    async def test_send_message_circuit_breaker_open(self, mock_settings):
        mock_settings.nexgai_enabled = True
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = "svc@test.com"
        mock_settings.nexgai_service_password = "pass"
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 1
        mock_settings.nexgai_circuit_breaker_cooldown = 9999
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        client.circuit_breaker.record_failure()
        assert client.circuit_breaker.is_open

        result = await client.send_message("Hello", "sess-123")
        assert result is None

    @patch("app.services.nexgai_client.settings")
    async def test_send_message_failure_trips_breaker(self, mock_settings):
        mock_settings.nexgai_enabled = True
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = "svc@test.com"
        mock_settings.nexgai_service_password = "pass"
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 2
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        client._access_token = "test-token"
        client._token_expires_at = time.monotonic() + 3600

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client_cls.return_value = mock_client

            result1 = await client.send_message("Hello", "sess-1")
            result2 = await client.send_message("Hello", "sess-1")

        assert result1 is None
        assert result2 is None
        assert client.circuit_breaker.is_open


@pytest.mark.asyncio
class TestNexgAIAgentDiscovery:
    @patch("app.services.nexgai_client.settings")
    async def test_list_agents_success(self, mock_settings):
        mock_settings.nexgai_enabled = True
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = "svc@test.com"
        mock_settings.nexgai_service_password = "pass"
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 3
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        client._access_token = "test-token"
        client._token_expires_at = time.monotonic() + 3600

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "agent_id": "agent-1",
                "name": "billing_agent",
                "display_name": "Billing Agent",
                "description": "Handles billing queries",
                "type": "reactive",
                "status": "active",
                "verticals": ["telecom"],
            },
            {
                "agent_id": "agent-2",
                "name": "support_agent",
                "display_name": "Support Agent",
                "description": "Customer support",
                "type": "conversational",
                "status": "active",
                "verticals": ["telecom"],
            },
        ]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            agents = await client.list_agents()

        assert len(agents) == 2
        assert agents[0].name == "billing_agent"
        assert agents[0].display_name == "Billing Agent"
        assert agents[1].agent_type == "conversational"

    @patch("app.services.nexgai_client.settings")
    async def test_list_agents_uses_cache(self, mock_settings):
        mock_settings.nexgai_enabled = True
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = "svc@test.com"
        mock_settings.nexgai_service_password = "pass"
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 3
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        # Pre-populate cache
        client._agent_cache = [
            NexgAIAgent(
                agent_id="cached-1",
                name="cached",
                display_name="Cached",
                description="From cache",
                agent_type="reactive",
                status="active",
            )
        ]
        client._agent_cache_at = time.monotonic()  # Just cached

        agents = await client.list_agents()
        assert len(agents) == 1
        assert agents[0].agent_id == "cached-1"

    @patch("app.services.nexgai_client.settings")
    async def test_get_agent_summary(self, mock_settings):
        mock_settings.nexgai_enabled = True
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = "svc@test.com"
        mock_settings.nexgai_service_password = "pass"
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 3
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        client._agent_cache = [
            NexgAIAgent("a1", "test", "Test Agent", "Does stuff", "reactive", "active"),
        ]
        client._agent_cache_at = time.monotonic()

        summary = await client.get_agent_summary()
        assert "Test Agent" in summary
        assert "Does stuff" in summary


@pytest.mark.asyncio
class TestNexgAIHealthCheck:
    @patch("app.services.nexgai_client.settings")
    async def test_health_check_not_configured(self, mock_settings):
        mock_settings.nexgai_enabled = False
        mock_settings.nexgai_base_url = ""
        mock_settings.nexgai_service_user = ""
        mock_settings.nexgai_service_password = ""
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 3
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()
        result = await client.health_check()
        assert result is False

    @patch("app.services.nexgai_client.settings")
    async def test_health_check_success(self, mock_settings):
        mock_settings.nexgai_enabled = True
        mock_settings.nexgai_base_url = "http://nexgai:8000"
        mock_settings.nexgai_service_user = "svc@test.com"
        mock_settings.nexgai_service_password = "pass"
        mock_settings.nexgai_tenant_id = "default"
        mock_settings.nexgai_timeout = 30
        mock_settings.nexgai_stream_timeout = 120
        mock_settings.nexgai_circuit_breaker_threshold = 3
        mock_settings.nexgai_circuit_breaker_cooldown = 60
        mock_settings.nexgai_agent_cache_ttl = 300

        client = NexgAIClient()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await client.health_check()

        assert result is True


# ── NexgAI Agent Data Model Tests ──


class TestNexgAIAgent:
    def test_agent_dataclass(self):
        agent = NexgAIAgent(
            agent_id="test-1",
            name="test_agent",
            display_name="Test Agent",
            description="A test agent",
            agent_type="reactive",
            status="active",
            verticals=["telecom", "banking"],
        )
        assert agent.agent_id == "test-1"
        assert agent.verticals == ["telecom", "banking"]

    def test_agent_default_verticals(self):
        agent = NexgAIAgent(
            agent_id="t",
            name="t",
            display_name="T",
            description="",
            agent_type="",
            status="active",
        )
        assert agent.verticals == []
