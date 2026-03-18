"""Feedback collection service for the self-learning loop."""
from __future__ import annotations

import logging
import uuid

from app.storage.database import Database

logger = logging.getLogger(__name__)


class FeedbackService:
    """Handles feedback submission and retrieval."""

    def __init__(self, database: Database):
        self.db = database

    async def submit(
        self,
        message_id: int,
        conversation_id: str,
        user_id: str,
        rating: str,
        comment: str = "",
        source: str = "local",
        agent_name: str | None = None,
    ) -> str:
        """Submit feedback for a message. Returns the feedback ID."""
        if rating not in ("up", "down"):
            raise ValueError("rating must be 'up' or 'down'")

        feedback_id = str(uuid.uuid4())
        await self.db.add_feedback(
            feedback_id=feedback_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            rating=rating,
            comment=comment,
            source=source,
            agent_name=agent_name,
        )

        try:
            await self.db.log_usage_event(
                event_type="feedback",
                user_id=user_id,
                skill_name=agent_name,
                success=True,
                metadata={"rating": rating, "message_id": message_id},
            )
        except Exception:
            pass

        logger.info("Feedback %s: %s on message %d by %s", feedback_id, rating, message_id, user_id)
        return feedback_id

    async def get_stats(self, period_hours: int = 24, source: str | None = None) -> dict:
        return await self.db.get_feedback_stats(period_hours, source)
