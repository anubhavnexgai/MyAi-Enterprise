"""Tests for the self-learning loop: feedback, learning engine, and learning routes."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.learning.feedback_service import FeedbackService
from app.learning.engine import LearningEngine
from app.storage.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    """Create a temporary database with all tables."""
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    await database.init()
    return database


@pytest_asyncio.fixture
async def feedback_svc(db):
    return FeedbackService(db)


# ── Feedback Service Tests ──


class TestFeedbackService:
    @pytest.mark.asyncio
    async def test_submit_thumbs_up(self, feedback_svc, db):
        # Create a conversation and message first
        conv = await db.get_or_create_conversation("user-1")
        from app.storage.models import Message, Role
        msg_id = await db.add_message(conv.id, Message(role=Role.ASSISTANT, content="Hello"))

        fb_id = await feedback_svc.submit(
            message_id=msg_id,
            conversation_id=conv.id,
            user_id="user-1",
            rating="up",
        )
        assert fb_id  # UUID string returned

    @pytest.mark.asyncio
    async def test_submit_thumbs_down_with_comment(self, feedback_svc, db):
        conv = await db.get_or_create_conversation("user-1")
        from app.storage.models import Message, Role
        msg_id = await db.add_message(conv.id, Message(role=Role.ASSISTANT, content="Wrong answer"))

        fb_id = await feedback_svc.submit(
            message_id=msg_id,
            conversation_id=conv.id,
            user_id="user-1",
            rating="down",
            comment="This is incorrect",
        )
        assert fb_id

    @pytest.mark.asyncio
    async def test_submit_invalid_rating_raises(self, feedback_svc, db):
        with pytest.raises(ValueError, match="rating must be"):
            await feedback_svc.submit(
                message_id=1,
                conversation_id="conv-1",
                user_id="user-1",
                rating="maybe",
            )

    @pytest.mark.asyncio
    async def test_get_stats(self, feedback_svc, db):
        conv = await db.get_or_create_conversation("user-1")
        from app.storage.models import Message, Role
        msg1 = await db.add_message(conv.id, Message(role=Role.ASSISTANT, content="Good"))
        msg2 = await db.add_message(conv.id, Message(role=Role.ASSISTANT, content="Bad"))
        msg3 = await db.add_message(conv.id, Message(role=Role.ASSISTANT, content="OK"))

        await feedback_svc.submit(msg1, conv.id, "user-1", "up")
        await feedback_svc.submit(msg2, conv.id, "user-1", "down")
        await feedback_svc.submit(msg3, conv.id, "user-1", "up")

        stats = await feedback_svc.get_stats(period_hours=24)
        assert stats["total"] == 3
        assert stats["thumbs_up"] == 2
        assert stats["thumbs_down"] == 1
        assert stats["satisfaction_pct"] == 66.7

    @pytest.mark.asyncio
    async def test_get_stats_filtered_by_source(self, feedback_svc, db):
        conv = await db.get_or_create_conversation("user-1")
        from app.storage.models import Message, Role
        msg1 = await db.add_message(conv.id, Message(role=Role.ASSISTANT, content="Local"))
        msg2 = await db.add_message(conv.id, Message(role=Role.ASSISTANT, content="NexgAI"))

        await feedback_svc.submit(msg1, conv.id, "user-1", "up", source="local")
        await feedback_svc.submit(msg2, conv.id, "user-1", "down", source="nexgai")

        local_stats = await feedback_svc.get_stats(period_hours=24, source="local")
        assert local_stats["total"] == 1
        assert local_stats["thumbs_up"] == 1

        nexgai_stats = await feedback_svc.get_stats(period_hours=24, source="nexgai")
        assert nexgai_stats["total"] == 1
        assert nexgai_stats["thumbs_down"] == 1

    @pytest.mark.asyncio
    async def test_get_stats_empty(self, feedback_svc):
        stats = await feedback_svc.get_stats()
        assert stats["total"] == 0
        assert stats["satisfaction_pct"] == 0.0


# ── Learning Engine Tests ──


class TestLearningEngine:
    @pytest.mark.asyncio
    async def test_run_cycle_no_feedback(self, db):
        ollama = MagicMock()
        engine = LearningEngine(db, ollama)
        summary = await engine.run_cycle()
        assert summary["prompt_refinements"] == 0
        assert summary["response_improvements"] == 0
        assert summary["knowledge_expansions"] == 0

    @pytest.mark.asyncio
    async def test_run_cycle_generates_prompt_refinement(self, db):
        """With enough negative local feedback, engine should generate a prompt refinement."""
        conv = await db.get_or_create_conversation("user-1")
        from app.storage.models import Message, Role

        # Create 3 Q&A pairs with negative feedback
        for i in range(3):
            user_msg_id = await db.add_message(conv.id, Message(role=Role.USER, content=f"Question {i}"))
            asst_msg_id = await db.add_message(conv.id, Message(role=Role.ASSISTANT, content=f"Bad answer {i}"))
            await db.add_feedback(
                str(uuid.uuid4()), asst_msg_id, conv.id, "user-1",
                "down", source="local",
            )

        ollama = MagicMock()
        ollama.chat = AsyncMock(return_value={
            "message": {"content": "Improve the prompt by adding XYZ context."}
        })

        engine = LearningEngine(db, ollama)
        with patch("app.learning.engine.settings") as mock_settings:
            mock_settings.learning_interval_hours = 24
            mock_settings.learning_min_negative_feedback = 3
            summary = await engine.run_cycle()

        assert summary["prompt_refinements"] == 1
        ollama.chat.assert_called_once()

        # Verify learning entry was created
        entries = await db.get_learning_entries(status="pending")
        prompt_entries = [e for e in entries if e["entry_type"] == "prompt_refinement"]
        assert len(prompt_entries) == 1
        assert "XYZ" in prompt_entries[0]["suggested_improvement"]

    @pytest.mark.asyncio
    async def test_run_cycle_generates_nexgai_response_improvement(self, db):
        """NexgAI negative feedback should create response_improvement entries."""
        conv = await db.get_or_create_conversation("user-1")
        from app.storage.models import Message, Role

        for i in range(2):
            await db.add_message(conv.id, Message(role=Role.USER, content=f"Q {i}"))
            asst_id = await db.add_message(conv.id, Message(role=Role.ASSISTANT, content=f"NexgAI bad {i}"))
            await db.add_feedback(
                str(uuid.uuid4()), asst_id, conv.id, "user-1",
                "down", source="nexgai", agent_name="BillingBot",
            )

        ollama = MagicMock()
        engine = LearningEngine(db, ollama)
        with patch("app.learning.engine.settings") as mock_settings:
            mock_settings.learning_interval_hours = 24
            mock_settings.learning_min_negative_feedback = 5  # high threshold so local doesn't trigger
            summary = await engine.run_cycle()

        assert summary["response_improvements"] == 1
        entries = await db.get_learning_entries(entry_type="response_improvement")
        assert entries[0]["agent_name"] == "BillingBot"

    @pytest.mark.asyncio
    async def test_run_cycle_generates_knowledge_expansion(self, db):
        """Positive local feedback should create knowledge_expansion entries."""
        conv = await db.get_or_create_conversation("user-1")
        from app.storage.models import Message, Role

        user_id = await db.add_message(conv.id, Message(role=Role.USER, content="How do I reset my badge?"))
        asst_id = await db.add_message(conv.id, Message(
            role=Role.ASSISTANT,
            content="Go to the security desk on floor 2 and bring your employee ID. They process it in 15 minutes."
        ))
        await db.add_feedback(
            str(uuid.uuid4()), asst_id, conv.id, "user-1",
            "up", source="local",
        )

        ollama = MagicMock()
        engine = LearningEngine(db, ollama)
        with patch("app.learning.engine.settings") as mock_settings:
            mock_settings.learning_interval_hours = 24
            mock_settings.learning_min_negative_feedback = 99
            summary = await engine.run_cycle()

        assert summary["knowledge_expansions"] == 1


# ── Database Learning Methods Tests ──


class TestDatabaseLearningMethods:
    @pytest.mark.asyncio
    async def test_add_and_get_learning_entries(self, db):
        entry_id = str(uuid.uuid4())
        await db.add_learning_entry({
            "id": entry_id,
            "entry_type": "prompt_refinement",
            "source": "local",
            "trigger_feedback_ids": json.dumps(["fb-1", "fb-2"]),
            "original_query": "test query",
            "original_response": "test response",
            "suggested_improvement": "improve this",
        })

        entries = await db.get_learning_entries(status="pending")
        assert len(entries) == 1
        assert entries[0]["id"] == entry_id
        assert entries[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_update_learning_entry(self, db):
        entry_id = str(uuid.uuid4())
        await db.add_learning_entry({
            "id": entry_id,
            "entry_type": "prompt_refinement",
            "source": "local",
            "trigger_feedback_ids": "[]",
            "original_query": "q",
            "original_response": "r",
            "suggested_improvement": "s",
        })

        updated = await db.update_learning_entry(entry_id, status="approved", reviewed_by="admin-1")
        assert updated is True

        entries = await db.get_learning_entries(status="approved")
        assert len(entries) == 1
        assert entries[0]["reviewed_by"] == "admin-1"

    @pytest.mark.asyncio
    async def test_prompt_versions(self, db):
        # No active prompt initially
        active = await db.get_active_prompt("local")
        assert active is None

        # Add a version
        await db.add_prompt_version({
            "id": str(uuid.uuid4()),
            "source": "local",
            "prompt_text": "You are a helpful assistant v2.",
            "created_by": "admin-1",
        })

        active = await db.get_active_prompt("local")
        assert active == "You are a helpful assistant v2."

        # Add another version — should deactivate the first
        await db.add_prompt_version({
            "id": str(uuid.uuid4()),
            "source": "local",
            "prompt_text": "You are a helpful assistant v3.",
            "created_by": "admin-1",
        })

        active = await db.get_active_prompt("local")
        assert active == "You are a helpful assistant v3."

        versions = await db.get_prompt_versions("local")
        assert len(versions) == 2
        active_count = sum(1 for v in versions if v["is_active"])
        assert active_count == 1

    @pytest.mark.asyncio
    async def test_satisfaction_snapshots(self, db):
        await db.save_satisfaction_snapshot("2026-03-18", "all", {
            "total": 10, "thumbs_up": 8, "thumbs_down": 2, "satisfaction_pct": 80.0
        })

        trend = await db.get_satisfaction_trend(30)
        assert len(trend) == 1
        assert trend[0]["satisfaction_pct"] == 80.0

    @pytest.mark.asyncio
    async def test_add_message_returns_id(self, db):
        conv = await db.get_or_create_conversation("user-1")
        from app.storage.models import Message, Role
        msg_id = await db.add_message(conv.id, Message(role=Role.USER, content="test"))
        assert isinstance(msg_id, int)
        assert msg_id > 0
