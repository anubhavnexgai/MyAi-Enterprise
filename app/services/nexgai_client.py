"""NexgAI Platform integration client.

Handles authentication, message routing, SSE streaming, agent discovery,
and circuit-breaker based graceful degradation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreaker:
    """Simple circuit breaker: after *threshold* consecutive failures, open
    for *cooldown_seconds* before allowing another attempt."""

    threshold: int = 3
    cooldown_seconds: int = 60

    _failures: int = 0
    _opened_at: float = 0.0

    @property
    def is_open(self) -> bool:
        if self._failures < self.threshold:
            return False
        # Check if cooldown has elapsed
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            # Half-open: allow one attempt
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = 0.0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit breaker OPEN after %d failures (cooldown %ds)",
                self._failures,
                self.cooldown_seconds,
            )


@dataclass
class NexgAIAgent:
    """Cached representation of a NexgAI platform agent."""

    agent_id: str
    name: str
    display_name: str
    description: str
    agent_type: str
    status: str
    verticals: list[str] = field(default_factory=list)


class NexgAIClient:
    """HTTP client for the NexgAI Enterprise Agentization Platform.

    Features:
    - SSO-based service account authentication with token refresh
    - Send/stream messages via the v3 unified chat API
    - Agent discovery with in-memory caching
    - Circuit breaker for graceful degradation
    """

    def __init__(self) -> None:
        self.base_url = settings.nexgai_base_url.rstrip("/")
        self.tenant_id = settings.nexgai_tenant_id
        self.timeout = settings.nexgai_timeout
        self.stream_timeout = settings.nexgai_stream_timeout

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float = 0.0

        self._agent_cache: list[NexgAIAgent] = []
        self._agent_cache_at: float = 0.0
        self._agent_cache_ttl = settings.nexgai_agent_cache_ttl

        self.circuit_breaker = CircuitBreaker(
            threshold=settings.nexgai_circuit_breaker_threshold,
            cooldown_seconds=settings.nexgai_circuit_breaker_cooldown,
        )

    @property
    def is_configured(self) -> bool:
        return bool(
            settings.nexgai_enabled
            and settings.nexgai_base_url
            and settings.nexgai_service_user
        )

    @property
    def is_available(self) -> bool:
        return self.is_configured and not self.circuit_breaker.is_open

    # ── Authentication ──

    async def authenticate(self) -> bool:
        """Authenticate with NexgAI using SSO login flow.

        Since NexgAI uses SSO (redirect-based), for service-to-service we
        use a direct token endpoint if available, or fall back to storing
        pre-provisioned tokens via environment variables.
        """
        if not self.is_configured:
            return False

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Try the SSO refresh flow first if we have a refresh token
                if self._refresh_token:
                    resp = await client.post(
                        f"{self.base_url}/api/auth/sso/refresh",
                        json={"refresh_token": self._refresh_token},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        self._access_token = data["access_token"]
                        self._refresh_token = data.get("refresh_token", self._refresh_token)
                        self._token_expires_at = time.monotonic() + data.get("expires_in", 3600) - 60
                        logger.info("NexgAI token refreshed successfully")
                        return True

                # For service account auth, use a direct API key / token approach.
                # NexgAI's SSO is redirect-based; in production the service account
                # would have a pre-provisioned JWT or API key.
                # Here we support a simple credential-based login endpoint.
                resp = await client.post(
                    f"{self.base_url}/api/auth/service-login",
                    json={
                        "email": settings.nexgai_service_user,
                        "password": settings.nexgai_service_password,
                        "tenant_id": self.tenant_id,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._access_token = data["access_token"]
                    self._refresh_token = data.get("refresh_token")
                    self._token_expires_at = time.monotonic() + data.get("expires_in", 3600) - 60
                    logger.info("NexgAI service account authenticated")
                    return True

                logger.error(
                    "NexgAI auth failed: %d %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False

        except Exception as exc:
            logger.error("NexgAI auth error: %s", exc)
            return False

    async def _ensure_token(self) -> str | None:
        """Return a valid access token, refreshing if needed."""
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token
        if await self.authenticate():
            return self._access_token
        return None

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    # ── Health Check ──

    async def health_check(self) -> bool:
        """Check if NexgAI platform is reachable."""
        if not self.is_configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/api/v3/chat/health")
                return resp.status_code == 200
        except Exception:
            return False

    # ── Chat API ──

    async def create_session(self) -> str | None:
        """Create a new NexgAI chat session and return its ID."""
        token = await self._ensure_token()
        if not token:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v3/chat/session",
                    params={"tenant_id": self.tenant_id},
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                return resp.json().get("session_id")
        except Exception as exc:
            logger.error("NexgAI create_session failed: %s", exc)
            return None

    async def send_message(
        self,
        message: str,
        session_id: str,
        user_id: str | None = None,
        user_name: str | None = None,
    ) -> dict | None:
        """Send a message to NexgAI and return the response dict.

        Returns None if the circuit breaker is open or the request fails.
        """
        if self.circuit_breaker.is_open:
            logger.debug("NexgAI circuit breaker is open, skipping")
            return None

        token = await self._ensure_token()
        if not token:
            self.circuit_breaker.record_failure()
            return None

        payload = {
            "message": message,
            "session_id": session_id,
            "tenant_id": self.tenant_id,
            "channel": "api",
            "user_type": "employee",
            "authenticated": True,
        }
        if user_id:
            payload["user_id"] = user_id
        if user_name:
            payload["metadata"] = {"myai_user": user_name}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v3/chat",
                    json=payload,
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()

            self.circuit_breaker.record_success()
            return data

        except Exception as exc:
            logger.error("NexgAI send_message failed: %s", exc)
            self.circuit_breaker.record_failure()
            return None

    async def stream_message(
        self,
        message: str,
        session_id: str,
        user_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """Stream a response from NexgAI via SSE, yielding event dicts.

        Each yielded dict has at least {"event": str, ...} with event types:
        session, status, chunk, complete, error.
        """
        if self.circuit_breaker.is_open:
            yield {"event": "error", "error": "Circuit breaker is open"}
            return

        token = await self._ensure_token()
        if not token:
            self.circuit_breaker.record_failure()
            yield {"event": "error", "error": "Authentication failed"}
            return

        payload = {
            "message": message,
            "session_id": session_id,
            "tenant_id": self.tenant_id,
            "channel": "api",
            "user_type": "employee",
            "authenticated": True,
        }
        if user_id:
            payload["user_id"] = user_id

        try:
            async with httpx.AsyncClient(timeout=self.stream_timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/v3/chat/stream",
                    json=payload,
                    headers=self._auth_headers(),
                ) as resp:
                    resp.raise_for_status()
                    current_event = "message"
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("event:"):
                            current_event = line[6:].strip()
                            continue
                        if line.startswith("data:"):
                            import json
                            try:
                                data = json.loads(line[5:].strip())
                                data["event"] = current_event
                                yield data
                            except json.JSONDecodeError:
                                continue

            self.circuit_breaker.record_success()

        except Exception as exc:
            logger.error("NexgAI stream failed: %s", exc)
            self.circuit_breaker.record_failure()
            yield {"event": "error", "error": str(exc)}

    # ── Agent Discovery ──

    async def list_agents(self) -> list[NexgAIAgent]:
        """Fetch available agents from NexgAI, with caching."""
        now = time.monotonic()
        if self._agent_cache and (now - self._agent_cache_at) < self._agent_cache_ttl:
            return self._agent_cache

        token = await self._ensure_token()
        if not token:
            return self._agent_cache  # Return stale cache if available

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/agenthub/agents",
                    params={"status": "active"},
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                agents_data = resp.json()

            self._agent_cache = [
                NexgAIAgent(
                    agent_id=a.get("agent_id", ""),
                    name=a.get("name", ""),
                    display_name=a.get("display_name", a.get("name", "")),
                    description=a.get("description", ""),
                    agent_type=a.get("type", ""),
                    status=a.get("status", "active"),
                    verticals=a.get("verticals", []),
                )
                for a in (agents_data if isinstance(agents_data, list) else [])
            ]
            self._agent_cache_at = now
            logger.info("Refreshed NexgAI agent cache: %d agents", len(self._agent_cache))
            return self._agent_cache

        except Exception as exc:
            logger.error("NexgAI list_agents failed: %s", exc)
            return self._agent_cache

    async def get_agent_summary(self) -> str:
        """Return a human-readable summary of available NexgAI agents."""
        agents = await self.list_agents()
        if not agents:
            return "No NexgAI agents available."
        lines = ["**NexgAI Platform Agents:**"]
        for a in agents:
            lines.append(f"- **{a.display_name}**: {a.description}")
        return "\n".join(lines)
