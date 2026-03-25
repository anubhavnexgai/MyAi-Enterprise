"""AgentHub External Gateway client.

Communicates with the AgentHub external gateway API for agent discovery,
invocation, and auto-routed chat.  Completely standalone — does NOT modify
or depend on the existing NexgAI client.

Authentication: tenant-scoped API key via ``AGENTHUB_API_KEY``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit Breaker (same pattern as nexgai_client.CircuitBreaker)
# ---------------------------------------------------------------------------

@dataclass
class _CircuitBreaker:
    """After *threshold* consecutive failures, open for *cooldown_seconds*."""

    threshold: int = 3
    cooldown_seconds: int = 60

    _failures: int = 0
    _opened_at: float = 0.0

    @property
    def is_open(self) -> bool:
        if self._failures < self.threshold:
            return False
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            return False  # half-open — allow one attempt
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = 0.0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = time.monotonic()
            logger.warning(
                "AgentHub circuit breaker OPEN after %d failures (cooldown %ds)",
                self._failures,
                self.cooldown_seconds,
            )


# ---------------------------------------------------------------------------
# Cached agent entry
# ---------------------------------------------------------------------------

@dataclass
class CachedAgent:
    agent_id: str
    name: str
    display_name: str
    description: str
    agent_type: str
    status: str
    capabilities: list[str] = field(default_factory=list)
    authority_level: str = ""
    verticals: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AgentHub Client
# ---------------------------------------------------------------------------

class AgentHubClient:
    """HTTP client for the AgentHub external gateway.

    All public methods return *dicts* and never raise.  On error the dict
    contains ``{"ok": False, "error": "...", "status_code": int}``.
    """

    _AGENT_CACHE_TTL = 300  # 5 minutes

    def __init__(self) -> None:
        self._base_url: str = (settings.agenthub_base_url or "").rstrip("/")
        self._api_key: str = settings.agenthub_api_key or ""
        self._tenant_id: str = settings.agenthub_tenant_id or "enterprise_copilot"
        self._timeout: int = settings.agenthub_timeout

        self.circuit_breaker = _CircuitBreaker(threshold=3, cooldown_seconds=60)

        # Agent cache
        self._agent_cache: list[CachedAgent] = []
        self._agent_cache_at: float = 0.0

    # -- Properties ----------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """True when both the API key and base URL are set."""
        return bool(self._api_key and self._base_url)

    @property
    def is_available(self) -> bool:
        """Configured AND circuit breaker is closed (or half-open)."""
        return self.is_configured and not self.circuit_breaker.is_open

    # -- Internal helpers ----------------------------------------------------

    def _headers(
        self,
        user_id: str | None = None,
        roles: list[str] | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "X-Tenant-ID": self._tenant_id,
        }
        if user_id:
            headers["X-User-ID"] = user_id
        if roles:
            headers["X-User-Roles"] = ",".join(roles)
        return headers

    @staticmethod
    def _error_dict(
        message: str,
        status_code: int = 0,
    ) -> dict[str, Any]:
        return {"ok": False, "error": message, "status_code": status_code}

    def _handle_status(self, status_code: int, body: str) -> dict[str, Any] | None:
        """Return an error dict for known error codes, or None if OK."""
        if 200 <= status_code < 300:
            return None

        mapping: dict[int, str] = {
            401: "AgentHub authentication failed — check API key",
            403: "You don't have access to this capability",
            404: "Requested agent or endpoint not found",
            429: "Too many requests — please try again shortly",
        }

        if status_code in mapping:
            msg = mapping[status_code]
        elif status_code >= 500:
            msg = "AgentHub server error"
        else:
            msg = f"AgentHub returned HTTP {status_code}"

        # 500+ and timeouts count as circuit-breaker failures
        if status_code >= 500:
            self.circuit_breaker.record_failure()

        logger.error("AgentHub HTTP %d: %s", status_code, body[:300])
        return self._error_dict(msg, status_code)

    # -- Public API ----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """GET /health — check if AgentHub is reachable."""
        if not self.is_configured:
            return self._error_dict("AgentHub not configured")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self._base_url}/health",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            if resp.status_code == 200:
                return {"ok": True, "status": "healthy"}
            return self._error_dict(f"Health check returned {resp.status_code}", resp.status_code)
        except Exception as exc:
            logger.error("AgentHub health_check error: %s", exc)
            return self._error_dict(str(exc))

    async def discover_agents(
        self,
        user_id: str,
        tenant_id: str | None = None,
        roles: list[str] | None = None,
    ) -> dict[str, Any]:
        """GET /external/agenthub/agents — discover agents the user may access.

        Returns ``{"ok": True, "agents": [CachedAgent, ...]}`` or an error dict.
        Uses an in-memory cache with a 5-minute TTL.
        """
        if not self.is_available:
            if not self.is_configured:
                return self._error_dict("AgentHub not configured")
            return self._error_dict("AgentHub circuit breaker is open")

        # Check cache
        now = time.monotonic()
        if self._agent_cache and (now - self._agent_cache_at) < self._AGENT_CACHE_TTL:
            logger.debug("Returning cached agent list (%d agents)", len(self._agent_cache))
            return {"ok": True, "agents": list(self._agent_cache)}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/external/agenthub/agents",
                    headers=self._headers(user_id, roles),
                    params={"tenant_id": tenant_id or self._tenant_id},
                )

            err = self._handle_status(resp.status_code, resp.text)
            if err:
                # Return stale cache if available
                if self._agent_cache:
                    logger.warning("Returning stale agent cache after error")
                    return {"ok": True, "agents": list(self._agent_cache), "stale": True}
                return err

            data = resp.json()
            agents_raw = data if isinstance(data, list) else data.get("agents", [])

            self._agent_cache = [
                CachedAgent(
                    agent_id=a.get("agent_id", ""),
                    name=a.get("name", ""),
                    display_name=a.get("display_name", a.get("name", "")),
                    description=a.get("description", ""),
                    agent_type=a.get("type", a.get("agent_type", "")),
                    status=a.get("status", "active"),
                    capabilities=a.get("capabilities", []),
                    authority_level=a.get("authority_level", ""),
                    verticals=a.get("verticals", []),
                )
                for a in agents_raw
            ]
            self._agent_cache_at = now
            self.circuit_breaker.record_success()
            logger.info("AgentHub agent cache refreshed: %d agents", len(self._agent_cache))
            return {"ok": True, "agents": list(self._agent_cache)}

        except Exception as exc:
            self.circuit_breaker.record_failure()
            logger.error("AgentHub discover_agents error: %s", exc)
            if self._agent_cache:
                return {"ok": True, "agents": list(self._agent_cache), "stale": True}
            return self._error_dict(str(exc))

    async def invoke_agent(
        self,
        agent_id: str,
        message: str,
        user_id: str,
        tenant_id: str | None = None,
        roles: list[str] | None = None,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /external/agenthub/agents/{agent_id}/invoke — invoke a specific agent.

        Returns ``{"ok": True, "response": str, "agent_id": str, ...}``
        or an error dict.
        """
        if not self.is_available:
            if not self.is_configured:
                return self._error_dict("AgentHub not configured")
            return self._error_dict("AgentHub circuit breaker is open")

        payload: dict[str, Any] = {
            "message": message,
            "user_id": user_id,
            "tenant_id": tenant_id or self._tenant_id,
            "roles": roles or ["employee"],
        }
        if session_id:
            payload["session_id"] = session_id
        if context:
            payload["context"] = context

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/external/agenthub/agents/{agent_id}/invoke",
                    json=payload,
                    headers=self._headers(user_id, roles),
                )

            err = self._handle_status(resp.status_code, resp.text)
            if err:
                return err

            data = resp.json()
            self.circuit_breaker.record_success()
            return {
                "ok": True,
                "response": data.get("response", data.get("message", "")),
                "agent_id": agent_id,
                "agent_name": data.get("agent_name", agent_id),
                "raw": data,
            }

        except Exception as exc:
            self.circuit_breaker.record_failure()
            logger.error("AgentHub invoke_agent(%s) error: %s", agent_id, exc)
            return self._error_dict(str(exc))

    async def invoke_chat(
        self,
        message: str,
        user_id: str,
        tenant_id: str | None = None,
        roles: list[str] | None = None,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /external/agenthub/chat — auto-routed chat (AgentHub picks the agent).

        Returns ``{"ok": True, "response": str, "agent_used": str, ...}``
        or an error dict.
        """
        if not self.is_available:
            if not self.is_configured:
                return self._error_dict("AgentHub not configured")
            return self._error_dict("AgentHub circuit breaker is open")

        payload: dict[str, Any] = {
            "message": message,
            "user_id": user_id,
            "tenant_id": tenant_id or self._tenant_id,
            "roles": roles or ["employee"],
        }
        if session_id:
            payload["session_id"] = session_id
        if context:
            payload["context"] = context

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/external/agenthub/chat",
                    json=payload,
                    headers=self._headers(user_id, roles),
                )

            err = self._handle_status(resp.status_code, resp.text)
            if err:
                return err

            data = resp.json()
            self.circuit_breaker.record_success()
            return {
                "ok": True,
                "response": data.get("response", data.get("message", "")),
                "agent_used": data.get("agent_used", data.get("handled_by", "unknown")),
                "raw": data,
            }

        except Exception as exc:
            self.circuit_breaker.record_failure()
            logger.error("AgentHub invoke_chat error: %s", exc)
            return self._error_dict(str(exc))

    # -- Cache helpers -------------------------------------------------------

    def get_cached_agents(self) -> list[CachedAgent]:
        """Return the current in-memory agent cache (may be empty or stale)."""
        return list(self._agent_cache)

    def invalidate_cache(self) -> None:
        """Force the next ``discover_agents`` call to refresh from the gateway."""
        self._agent_cache.clear()
        self._agent_cache_at = 0.0
