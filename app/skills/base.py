"""Base class for all enterprise skills (NexgAI agent connectors)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SkillContext:
    """Context passed to a skill when it executes."""

    user_id: str
    user_name: str = "User"
    user_role: str = ""
    user_bio: str = ""
    channel_id: str = ""
    raw_message: str = ""
    # Extra metadata skills can use (e.g. ticket ID, meeting subject)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    """Standardized result returned by a skill."""

    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    # If the skill needs human approval before completing
    needs_approval: bool = False
    approval_prompt: str = ""
    # The source agent that handled this
    agent_name: str = ""


class BaseSkill(ABC):
    """Abstract base for enterprise skills.

    Each skill represents a connection to a NexgAI platform agent
    (e.g. VULCAN for IT, VESTA for HR). Skills can run locally
    (via Ollama) or connect to the NexgAI platform API.
    """

    # Subclasses must set these
    name: str = ""
    agent_name: str = ""  # NexgAI agent name (e.g. "VULCAN")
    description: str = ""
    # Keywords that help the router match user requests to this skill
    keywords: list[str] = []
    # Example prompts users might send
    examples: list[str] = []

    def __init__(self, ollama=None, database=None, platform_url: str = ""):
        self.ollama = ollama
        self.database = database
        self.platform_url = platform_url

    @abstractmethod
    async def execute(self, context: SkillContext, request: str) -> SkillResult:
        """Execute the skill with the given context and user request."""
        ...

    @abstractmethod
    def can_handle(self, text: str) -> float:
        """Return a confidence score (0.0-1.0) for whether this skill can handle the request.

        The router uses this to pick the best skill when multiple might match.
        """
        ...

    def _keyword_score(self, text: str) -> float:
        """Helper: score based on keyword matches.

        Scoring: 1 match = 0.3, 2 matches = 0.5, 3+ = 0.7.
        Multi-word keyword matches count double.
        """
        low = text.lower()
        score = 0.0
        for kw in self.keywords:
            if kw in low:
                # Multi-word keywords are more specific, worth more
                if " " in kw:
                    score += 0.35
                else:
                    score += 0.2
        return min(score, 1.0)

    async def _ask_ollama(self, system: str, user_prompt: str) -> str:
        """Helper: ask Ollama with a system prompt and user prompt."""
        if not self.ollama:
            return "(Ollama not available)"
        try:
            result = await self.ollama.chat(messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ])
            return result.get("message", {}).get("content", "").strip()
        except Exception as e:
            logger.error(f"Skill {self.name} Ollama call failed: {e}")
            return f"Error generating response: {e}"
