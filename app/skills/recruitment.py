"""FALCON — AI Talent Scout.

Handles: job posting drafting, candidate screening criteria, interview scheduling,
hiring pipeline questions, recruitment analytics.
"""

from __future__ import annotations

from app.skills.base import BaseSkill, SkillContext, SkillResult

SYSTEM_PROMPT = """You are FALCON, an AI Talent Scout and Recruitment Specialist within the enterprise.
You help with recruitment and hiring tasks including:
- Job description drafting and optimization
- Candidate screening criteria definition
- Interview question preparation
- Hiring pipeline status and analytics
- Offer letter drafting assistance
- Onboarding checklist creation
- Recruitment strategy recommendations
- Diversity and inclusion in hiring guidance
- Employer branding content

You are speaking to: {user_name} ({user_role})

Rules:
- Write inclusive, bias-free job descriptions
- Focus on skills and competencies over credentials
- For interview questions, ensure they are behavioral and role-relevant
- Always consider diversity and fair hiring practices
- Provide data-driven recruitment recommendations when possible
- For offer details, note that final terms need HR/management approval"""


class RecruitmentSkill(BaseSkill):
    name = "recruitment"
    agent_name = "FALCON"
    description = "Recruitment: job posts, screening, interviews, hiring pipeline"
    keywords = [
        "hire", "hiring", "recruit", "recruitment", "candidate",
        "job description", "job posting", "job opening", "position",
        "interview", "screening", "resume", "cv",
        "offer letter", "compensation", "salary band",
        "onboarding", "new hire", "headcount",
        "talent", "sourcing", "pipeline",
        "reference check", "background check",
        "jd", "req", "requisition",
    ]
    examples = [
        "Write a job description for a senior backend engineer",
        "What interview questions should I ask for a product manager role?",
        "Help me create a screening rubric for frontend candidates",
        "Draft an offer letter for the selected candidate",
        "What's the typical hiring timeline for engineering roles?",
    ]

    def can_handle(self, text: str) -> float:
        score = self._keyword_score(text)
        low = text.lower()
        if any(p in low for p in ["job description", "interview questions", "hire a", "recruiting"]):
            score = max(score, 0.85)
        if any(p in low for p in ["candidate", "offer letter", "onboarding new"]):
            score = max(score, 0.6)
        return score

    async def execute(self, context: SkillContext, request: str) -> SkillResult:
        system = SYSTEM_PROMPT.format(
            user_name=context.user_name,
            user_role=context.user_role or "Hiring Manager",
        )

        response = await self._ask_ollama(system, request)

        return SkillResult(
            success=True,
            message=response,
        )
