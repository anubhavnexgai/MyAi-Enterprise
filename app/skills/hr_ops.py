"""VESTA — AI HR Operations Specialist.

Handles: HR inquiries, leave management, benefits questions, policy guidance,
onboarding coordination, employee support.
"""

from __future__ import annotations

from app.skills.base import BaseSkill, SkillContext, SkillResult

SYSTEM_PROMPT = """You are VESTA, an AI HR Operations Specialist within the enterprise.
You help employees with HR-related inquiries including:
- Leave requests and balance inquiries (PTO, sick leave, parental leave)
- Benefits information (health insurance, 401k, dental, vision)
- Company policies and employee handbook questions
- Onboarding support for new employees
- Payroll questions (pay dates, deductions, tax forms)
- Performance review process and timelines
- Training and development opportunities
- Workplace accommodations
- Employee referral programs

You are speaking to: {user_name} ({user_role})

Rules:
- Be warm, supportive, and clear
- For leave requests, confirm the type, dates, and note it needs manager approval
- For sensitive topics (termination, grievances, harassment), direct to HR leadership
- Provide specific policy references when possible
- If you don't know a company-specific policy, say so and suggest who to contact
- Never share other employees' personal information"""


class HROpsSkill(BaseSkill):
    name = "hr_ops"
    agent_name = "VESTA"
    description = "HR support: leave, benefits, policies, onboarding, payroll"
    keywords = [
        "leave", "pto", "vacation", "sick day", "sick leave", "time off",
        "day off", "days off", "holiday", "annual leave", "parental leave",
        "maternity", "paternity", "benefits", "health insurance", "dental",
        "vision", "401k", "retirement", "pension", "hsa", "fsa",
        "payroll", "pay", "salary", "paycheck", "deduction", "tax form",
        "w2", "w-2", "onboarding", "new hire", "first day",
        "policy", "handbook", "dress code", "remote work", "work from home",
        "wfh", "hybrid", "office", "hr", "human resources",
        "performance review", "appraisal", "training", "development",
        "referral", "refer someone", "accommodation", "disability",
        "expense", "reimbursement", "travel policy",
    ]
    examples = [
        "How many PTO days do I have left?",
        "I need to take sick leave tomorrow",
        "What's our parental leave policy?",
        "When is the next paycheck?",
        "How do I enroll in dental insurance?",
        "What's the remote work policy?",
    ]

    def can_handle(self, text: str) -> float:
        score = self._keyword_score(text)
        low = text.lower()
        if any(p in low for p in [
            "leave", "pto", "sick day", "time off", "vacation", "day off",
            "benefits", "health insurance", "payroll", "hr ",
        ]):
            score = max(score, 0.7)
        return min(score, 1.0)

    async def execute(self, context: SkillContext, request: str) -> SkillResult:
        system = SYSTEM_PROMPT.format(
            user_name=context.user_name,
            user_role=context.user_role or "Employee",
        )

        # Load user's stored contexts for company policy info
        extra_context = ""
        if self.database:
            contexts = await self.database.get_all_contexts(context.user_id)
            policy_contexts = [c for c in contexts if any(
                kw in c["name"].lower() for kw in ["policy", "hr", "handbook", "leave", "benefit"]
            )]
            if policy_contexts:
                extra_context = "\n\n## Company Policy Context\n"
                for ctx in policy_contexts:
                    extra_context += f"\n### {ctx['name']}\n{ctx['content']}\n"

        full_system = system + extra_context
        response = await self._ask_ollama(full_system, request)

        needs_approval = any(
            kw in request.lower()
            for kw in ["leave request", "time off", "day off", "sick leave", "vacation"]
        )

        return SkillResult(
            success=True,
            message=response,
            needs_approval=needs_approval,
            approval_prompt="Leave requests require manager approval." if needs_approval else "",
        )
