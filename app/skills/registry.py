"""Skill registry — routes user requests to the appropriate enterprise agent skill."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.skills.base import BaseSkill, SkillContext, SkillResult

if TYPE_CHECKING:
    from app.auth.models import User
    from app.auth.rbac import RBACService
    from app.services.ollama import OllamaClient
    from app.storage.database import Database

logger = logging.getLogger(__name__)

# Minimum confidence threshold to route to a skill
ROUTING_THRESHOLD = 0.3


class SkillRegistry:
    """Registry of enterprise skills.

    Maintains all available skills and routes user requests
    to the best-matching one based on confidence scoring.
    """

    def __init__(self, ollama: OllamaClient | None = None, database: Database | None = None):
        self.ollama = ollama
        self.database = database
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        """Register a skill."""
        self._skills[skill.name] = skill
        logger.info(f"Registered enterprise skill: {skill.name} ({skill.agent_name})")

    def get_skill(self, name: str) -> BaseSkill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    @property
    def all_skills(self) -> list[BaseSkill]:
        return list(self._skills.values())

    @property
    def skill_names(self) -> list[str]:
        return list(self._skills.keys())

    def route(self, text: str) -> tuple[BaseSkill | None, float]:
        """Find the best skill for the given user text.

        All skills are available to every user.
        Returns (skill, confidence) or (None, 0.0) if no skill matches.
        """
        best_skill = None
        best_score = 0.0

        for skill in self._skills.values():
            score = skill.can_handle(text)
            if score > best_score:
                best_score = score
                best_skill = skill

        if best_score >= ROUTING_THRESHOLD:
            logger.info(
                f"Skill router: matched '{best_skill.name}' "
                f"({best_skill.agent_name}) with confidence {best_score:.2f}"
            )
            return best_skill, best_score

        return None, 0.0

    async def execute(
        self,
        text: str,
        context: SkillContext,
    ) -> SkillResult | None:
        """Route and execute the best skill for the user's request.

        All skills are accessible to every authenticated user.
        Returns None if no skill matches.
        """
        skill, confidence = self.route(text)
        if not skill:
            return None

        logger.info(
            f"Executing skill '{skill.name}' for user {context.user_id} "
            f"(confidence: {confidence:.2f})"
        )

        try:
            result = await skill.execute(context, text)
            result.agent_name = skill.agent_name
            return result
        except Exception as e:
            logger.error(f"Skill '{skill.name}' execution failed: {e}", exc_info=True)
            return SkillResult(
                success=False,
                message=f"The {skill.agent_name} agent encountered an error: {str(e)[:200]}",
                agent_name=skill.agent_name,
            )

    def get_skills_summary(self) -> str:
        """Return a formatted summary of all available skills."""
        if not self._skills:
            return "No enterprise skills available."

        lines = ["*Available Enterprise Skills:*\n"]
        for skill in self._skills.values():
            lines.append(f"*{skill.agent_name}* — {skill.description}")
        return "\n".join(lines)
