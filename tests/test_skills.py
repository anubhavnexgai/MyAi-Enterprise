"""Tests for the enterprise skills framework."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.skills.base import BaseSkill, SkillContext, SkillResult
from app.skills.registry import SkillRegistry, ROUTING_THRESHOLD
from app.skills.it_support import ITSupportSkill
from app.skills.hr_ops import HROpsSkill
from app.skills.finance import FinanceSkill
from app.skills.legal_compliance import LegalComplianceSkill
from app.skills.executive_assistant import ExecutiveAssistantSkill
from app.skills.recruitment import RecruitmentSkill
from app.skills.data_analytics import DataAnalyticsSkill
from app.skills.project_coordination import ProjectCoordinationSkill


# -- Fixtures --

@pytest.fixture
def mock_ollama():
    client = MagicMock()
    client.model = "llama3.1:8b"
    client.chat = AsyncMock(
        return_value={"message": {"content": "Here's how to fix that issue..."}}
    )
    return client


@pytest.fixture
def mock_database():
    db = MagicMock()
    db.get_user_profile = AsyncMock(return_value={
        "user_id": "U123", "name": "Anubhav", "role": "Engineer", "bio": "Backend dev"
    })
    db.get_all_contexts = AsyncMock(return_value=[])
    db.get_recent_meetings = AsyncMock(return_value=[])
    return db


@pytest.fixture
def registry(mock_ollama, mock_database):
    reg = SkillRegistry(ollama=mock_ollama, database=mock_database)
    for SkillClass in [
        ITSupportSkill, HROpsSkill, FinanceSkill, LegalComplianceSkill,
        ExecutiveAssistantSkill, RecruitmentSkill, DataAnalyticsSkill,
        ProjectCoordinationSkill,
    ]:
        reg.register(SkillClass(ollama=mock_ollama, database=mock_database))
    return reg


@pytest.fixture
def context():
    return SkillContext(
        user_id="U123",
        user_name="Anubhav",
        user_role="Engineer",
        channel_id="C456",
    )


# -- Registry Tests --

class TestSkillRegistry:
    def test_all_skills_registered(self, registry):
        assert len(registry.all_skills) == 8
        names = registry.skill_names
        assert "it_support" in names
        assert "hr_ops" in names
        assert "finance" in names
        assert "legal_compliance" in names
        assert "executive_assistant" in names
        assert "recruitment" in names
        assert "data_analytics" in names
        assert "project_coordination" in names

    def test_get_skill_by_name(self, registry):
        skill = registry.get_skill("it_support")
        assert skill is not None
        assert skill.agent_name == "VULCAN"

    def test_get_nonexistent_skill(self, registry):
        assert registry.get_skill("nonexistent") is None

    def test_skills_summary(self, registry):
        summary = registry.get_skills_summary()
        assert "VULCAN" in summary
        assert "VESTA" in summary
        assert "MIDAS" in summary


# -- Routing Tests --

class TestSkillRouting:
    def test_routes_password_reset_to_vulcan(self, registry):
        skill, score = registry.route("I forgot my password and can't login")
        assert skill is not None
        assert skill.agent_name == "VULCAN"
        assert score >= ROUTING_THRESHOLD

    def test_routes_pto_to_vesta(self, registry):
        skill, score = registry.route("How many PTO days do I have left?")
        assert skill is not None
        assert skill.agent_name == "VESTA"
        assert score >= ROUTING_THRESHOLD

    def test_routes_expense_to_midas(self, registry):
        skill, score = registry.route("I need to submit an expense report")
        assert skill is not None
        assert skill.agent_name == "MIDAS"
        assert score >= ROUTING_THRESHOLD

    def test_routes_contract_to_minerva(self, registry):
        skill, score = registry.route("Can you review this NDA for me?")
        assert skill is not None
        assert skill.agent_name == "MINERVA"
        assert score >= ROUTING_THRESHOLD

    def test_routes_scheduling_to_eklavya(self, registry):
        skill, score = registry.route("Schedule a meeting with the design team this Thursday")
        assert skill is not None
        assert skill.agent_name == "EKLAVYA"
        assert score >= ROUTING_THRESHOLD

    def test_routes_job_posting_to_falcon(self, registry):
        skill, score = registry.route("Write a job description for a senior backend engineer")
        assert skill is not None
        assert skill.agent_name == "FALCON"
        assert score >= ROUTING_THRESHOLD

    def test_routes_sql_to_apollo(self, registry):
        skill, score = registry.route("Write a SQL query to find top customers by revenue")
        assert skill is not None
        assert skill.agent_name == "APOLLO"
        assert score >= ROUTING_THRESHOLD

    def test_routes_sprint_to_janus(self, registry):
        skill, score = registry.route("Help me plan the next sprint")
        assert skill is not None
        assert skill.agent_name == "JANUS"
        assert score >= ROUTING_THRESHOLD

    def test_no_match_for_generic_chat(self, registry):
        skill, score = registry.route("Hello, how are you?")
        assert skill is None
        assert score < ROUTING_THRESHOLD

    def test_no_match_for_coding_question(self, registry):
        skill, score = registry.route("Write a Python function that reverses a string")
        # This shouldn't match any enterprise skill
        assert skill is None or score < ROUTING_THRESHOLD


# -- Execution Tests --

class TestSkillExecution:
    @pytest.mark.asyncio
    async def test_execute_it_support(self, registry, context):
        result = await registry.execute("My VPN isn't connecting", context)
        assert result is not None
        assert result.success is True
        assert result.agent_name == "VULCAN"
        assert len(result.message) > 0

    @pytest.mark.asyncio
    async def test_execute_hr_ops(self, registry, context):
        result = await registry.execute("I need to take sick leave tomorrow", context)
        assert result is not None
        assert result.success is True
        assert result.agent_name == "VESTA"

    @pytest.mark.asyncio
    async def test_execute_finance(self, registry, context):
        result = await registry.execute("Submit an expense report for client dinner", context)
        assert result is not None
        assert result.success is True
        assert result.agent_name == "MIDAS"

    @pytest.mark.asyncio
    async def test_execute_returns_none_for_no_match(self, registry, context):
        result = await registry.execute("Tell me a joke", context)
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_handles_ollama_error(self, registry, context, mock_ollama):
        mock_ollama.chat.side_effect = Exception("Connection refused")
        result = await registry.execute("I forgot my password and I'm locked out", context)
        assert result is not None
        # Skill catches Ollama error gracefully and returns error in message
        assert "error" in result.message.lower() or "connection refused" in result.message.lower()

    @pytest.mark.asyncio
    async def test_legal_includes_disclaimer(self, registry, context):
        result = await registry.execute("Review this NDA clause about non-compete", context)
        assert result is not None
        assert "not legal advice" in result.message.lower()

    @pytest.mark.asyncio
    async def test_leave_request_needs_approval(self, registry, context):
        result = await registry.execute("I need a leave request for next Friday", context)
        assert result is not None
        assert result.needs_approval is True


# -- Individual Skill Confidence Tests --

class TestSkillConfidence:
    def test_vulcan_high_on_password_reset(self):
        skill = ITSupportSkill()
        assert skill.can_handle("I need to reset my password") >= 0.5

    def test_vulcan_medium_on_install(self):
        skill = ITSupportSkill()
        assert skill.can_handle("How do I install Docker?") >= 0.3

    def test_vesta_high_on_pto(self):
        skill = HROpsSkill()
        assert skill.can_handle("What's my PTO balance?") >= 0.5

    def test_midas_high_on_expense(self):
        skill = FinanceSkill()
        assert skill.can_handle("Submit an expense report") >= 0.5

    def test_minerva_high_on_nda(self):
        skill = LegalComplianceSkill()
        assert skill.can_handle("Review this NDA") >= 0.5

    def test_eklavya_high_on_scheduling(self):
        skill = ExecutiveAssistantSkill()
        assert skill.can_handle("Schedule a meeting with the design team") >= 0.5

    def test_falcon_high_on_job_description(self):
        skill = RecruitmentSkill()
        assert skill.can_handle("Write a job description for a product manager") >= 0.5

    def test_apollo_high_on_sql(self):
        skill = DataAnalyticsSkill()
        assert skill.can_handle("Write a SQL query to analyze revenue") >= 0.3

    def test_janus_high_on_sprint(self):
        skill = ProjectCoordinationSkill()
        assert skill.can_handle("Help me plan the next sprint") >= 0.5

    def test_low_confidence_on_unrelated(self):
        """All skills should return low confidence for unrelated requests."""
        skills = [
            ITSupportSkill(), HROpsSkill(), FinanceSkill(),
            LegalComplianceSkill(), ExecutiveAssistantSkill(),
            RecruitmentSkill(), DataAnalyticsSkill(), ProjectCoordinationSkill(),
        ]
        for skill in skills:
            score = skill.can_handle("What's the weather like today?")
            assert score < ROUTING_THRESHOLD, f"{skill.name} scored {score} on unrelated request"


# -- Context Building Tests --

class TestSkillContext:
    @pytest.mark.asyncio
    async def test_hr_loads_policy_contexts(self, mock_ollama, mock_database):
        mock_database.get_all_contexts.return_value = [
            {"name": "HR-Leave-Policy", "content": "Employees get 20 PTO days per year."},
        ]
        skill = HROpsSkill(ollama=mock_ollama, database=mock_database)
        ctx = SkillContext(user_id="U1", user_name="Alice", user_role="Engineer")

        result = await skill.execute(ctx, "How many PTO days do I get?")
        assert result.success is True

        # Verify Ollama was called with the policy context
        call_args = mock_ollama.chat.call_args
        system_msg = call_args[1]["messages"][0]["content"]
        assert "20 PTO days" in system_msg

    @pytest.mark.asyncio
    async def test_eklavya_loads_meeting_history(self, mock_ollama, mock_database):
        mock_database.get_recent_meetings.return_value = [
            {"meeting_subject": "Sprint Review", "key_points": "Shipped v2.0", "ended_at": "2026-03-15"},
        ]
        mock_database.get_all_contexts.return_value = [
            {"name": "MyProject", "content": "Building an AI assistant"},
        ]
        skill = ExecutiveAssistantSkill(ollama=mock_ollama, database=mock_database)
        ctx = SkillContext(user_id="U1", user_name="Alice", user_role="PM")

        result = await skill.execute(ctx, "Prepare a briefing for my next meeting")
        assert result.success is True

        call_args = mock_ollama.chat.call_args
        system_msg = call_args[1]["messages"][0]["content"]
        assert "Sprint Review" in system_msg
        assert "MyProject" in system_msg

    @pytest.mark.asyncio
    async def test_janus_loads_project_contexts(self, mock_ollama, mock_database):
        mock_database.get_all_contexts.return_value = [
            {"name": "API-Migration", "content": "Migrating from REST to GraphQL by Q2"},
        ]
        skill = ProjectCoordinationSkill(ollama=mock_ollama, database=mock_database)
        ctx = SkillContext(user_id="U1", user_name="Alice")

        result = await skill.execute(ctx, "What's the project status?")
        assert result.success is True

        call_args = mock_ollama.chat.call_args
        system_msg = call_args[1]["messages"][0]["content"]
        assert "API-Migration" in system_msg
