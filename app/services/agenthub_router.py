"""AgentHub smart router.

Decides whether an incoming message should be routed to AgentHub (external
gateway) or handled locally by Ollama.  Returns ``None`` when the message
should stay local — the caller (AgentCore) continues with its normal flow.

This module is **completely standalone** and dormant until
``settings.agenthub_enabled`` is ``True`` and an ``AgentHubClient`` is
provided.
"""
from __future__ import annotations

import logging
import re
from typing import Any, TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.auth.models import User
    from app.services.agenthub_client import AgentHubClient, CachedAgent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Greeting / simple-question detector
# ---------------------------------------------------------------------------

_GREETING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(hi|hello|hey|good\s*(morning|afternoon|evening)|howdy|sup)\b", re.I),
    re.compile(r"^(thanks|thank\s*you|ty|cheers)\b", re.I),
    re.compile(r"^(bye|goodbye|see\s*you|later|gn)\b", re.I),
    re.compile(r"^(how\s+are\s+you|what'?s\s+up|how'?s\s+it\s+going)\b", re.I),
    re.compile(r"^(yes|no|ok|okay|sure|got\s*it|cool|nice)\s*[!?.]*$", re.I),
]

# Intent keywords → agent categories (lowercased)
_INTENT_KEYWORDS: dict[str, list[str]] = {
    "compliance": ["compliance", "audit", "regulation", "policy", "gdpr", "hipaa", "sox"],
    "hr": ["leave", "payroll", "salary", "benefits", "onboarding", "offboarding", "pto", "hr"],
    "service": ["ticket", "incident", "outage", "service desk", "helpdesk", "support ticket", "jira"],
    "faq": ["faq", "frequently asked", "knowledge base"],
    "analytics": ["analytics", "dashboard", "report", "metrics", "kpi", "statistics"],
}


def _is_greeting_or_simple(text: str) -> bool:
    """Return True for greetings, single-word replies, and trivial messages."""
    stripped = text.strip()
    if len(stripped) < 3:
        return True
    return any(p.search(stripped) for p in _GREETING_PATTERNS)


def _match_intent(text: str, agents: list[CachedAgent]) -> CachedAgent | None:
    """Simple keyword + description matching to find a suitable agent.

    Returns the best matching ``CachedAgent`` or ``None``.
    """
    lower = text.lower()

    # 1. Check keyword → category mapping
    matched_categories: set[str] = set()
    for category, keywords in _INTENT_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            matched_categories.add(category)

    if not matched_categories:
        # No obvious category match — skip AgentHub
        return None

    # 2. Find an agent whose name/description/capabilities overlap
    for agent in agents:
        agent_text = f"{agent.name} {agent.description} {' '.join(agent.capabilities)}".lower()
        for cat in matched_categories:
            if cat in agent_text or cat in agent.name.lower():
                logger.info(
                    "Intent match: category=%s → agent=%s (%s)",
                    cat, agent.agent_id, agent.display_name,
                )
                return agent

    # 3. Fallback: check if any agent description contains message keywords
    words = set(re.findall(r"\b[a-z]{4,}\b", lower))
    best: CachedAgent | None = None
    best_score = 0
    for agent in agents:
        desc_words = set(re.findall(r"\b[a-z]{4,}\b", agent.description.lower()))
        overlap = len(words & desc_words)
        if overlap > best_score:
            best_score = overlap
            best = agent

    if best and best_score >= 2:
        logger.info(
            "Description overlap match (score=%d) → agent=%s",
            best_score, best.agent_id if best else "none",
        )
        return best

    return None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class AgentHubRouter:
    """Decides whether to route a message to AgentHub or keep it local.

    Usage::

        router = AgentHubRouter(agenthub_client)
        result = await router.route(message, user_id, user, conversation_id)
        if result is None:
            # handle locally with Ollama
        else:
            # result contains {"text": ..., "source": "agenthub", ...}
    """

    def __init__(self, client: AgentHubClient) -> None:
        self._client = client

    async def route(
        self,
        message: str,
        user_id: str,
        user: User | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Attempt to route *message* through AgentHub.

        Returns a response dict on success, or ``None`` to indicate the
        caller should handle the message locally (Ollama).
        """
        # Guard: feature must be enabled and client configured
        if not settings.agenthub_enabled:
            return None

        if not self._client.is_available:
            logger.debug("AgentHub not available — falling back to local")
            return None

        # Don't send greetings / trivial messages to AgentHub
        if _is_greeting_or_simple(message):
            logger.debug("Greeting/simple message — keeping local")
            return None

        # Resolve user metadata
        roles: list[str] = ["employee"]
        tenant_id: str = settings.agenthub_tenant_id
        if user:
            roles = getattr(user, "roles", None) or ["employee"]
            if isinstance(roles, str):
                roles = [roles]
            tenant_id = getattr(user, "tenant_id", None) or tenant_id

        # Ensure we have a cached agent list
        agents = self._client.get_cached_agents()
        if not agents:
            disc = await self._client.discover_agents(user_id, tenant_id, roles)
            if disc.get("ok"):
                agents = disc.get("agents", [])
            if not agents:
                logger.debug("No agents discovered — keeping local")
                return None

        # Intent matching
        matched_agent = _match_intent(message, agents)

        if matched_agent:
            # Try direct agent invocation
            logger.info("Routing to AgentHub agent: %s", matched_agent.agent_id)
            result = await self._client.invoke_agent(
                agent_id=matched_agent.agent_id,
                message=message,
                user_id=user_id,
                tenant_id=tenant_id,
                roles=roles,
                session_id=conversation_id,
                context=self._build_context(user),
            )
        else:
            # No specific agent matched — try auto-routed chat
            logger.info("No specific agent matched — trying AgentHub auto-route")
            result = await self._client.invoke_chat(
                message=message,
                user_id=user_id,
                tenant_id=tenant_id,
                roles=roles,
                session_id=conversation_id,
                context=self._build_context(user),
            )

        # Evaluate result
        if not result.get("ok"):
            error = result.get("error", "")
            status = result.get("status_code", 0)

            # 403 → surface the access-denied message
            if status == 403:
                return {
                    "text": "You don't have access to this capability.",
                    "source": "agenthub",
                    "agent_name": None,
                }

            logger.warning("AgentHub returned error (%s) — falling back to local", error)
            return None

        response_text = result.get("response", "")
        if not response_text or not response_text.strip():
            logger.info("AgentHub returned empty response — falling back to local")
            return None

        agent_name = result.get("agent_name") or result.get("agent_used") or "AgentHub"
        return {
            "text": f"_Handled by *{agent_name}* (AgentHub)_\n\n{response_text}",
            "source": "agenthub",
            "agent_name": agent_name,
        }

    @staticmethod
    def _build_context(user: User | None) -> dict[str, Any]:
        """Build a context dict from the user object for AgentHub requests."""
        ctx: dict[str, Any] = {"source": "myai"}
        if user:
            if hasattr(user, "name") and user.name:
                ctx["user_name"] = user.name
            if hasattr(user, "email") and user.email:
                ctx["user_email"] = user.email
        return ctx
