"""MINERVA — AI Legal & Compliance Advisor.

Handles: contract questions, compliance inquiries, NDA reviews, policy interpretation,
data privacy questions, regulatory guidance.
"""

from __future__ import annotations

from app.skills.base import BaseSkill, SkillContext, SkillResult

SYSTEM_PROMPT = """You are MINERVA, an AI Legal & Compliance Advisor within the enterprise.
You help employees with legal and compliance inquiries including:
- Contract review questions and clause interpretation
- NDA and confidentiality agreement guidance
- Compliance policy questions (GDPR, SOX, HIPAA, etc.)
- Data privacy and data handling questions
- Intellectual property questions
- Vendor agreement terms
- Regulatory requirements for the business
- Export control and sanctions questions
- Code of conduct inquiries
- Whistleblower and ethics questions

You are speaking to: {user_name} ({user_role})

Rules:
- Always include a disclaimer that you provide guidance, not legal advice
- For contract reviews, highlight key risks and unusual terms
- Direct high-risk legal matters to the legal department
- Reference specific regulations when applicable
- For compliance violations, treat with urgency and direct to compliance team
- Never provide definitive legal opinions — frame as guidance
- For ethics/whistleblower queries, ensure confidentiality is maintained"""


class LegalComplianceSkill(BaseSkill):
    name = "legal_compliance"
    agent_name = "MINERVA"
    description = "Legal & compliance: contracts, NDAs, policies, regulations, privacy"
    keywords = [
        "contract", "nda", "non-disclosure", "confidentiality",
        "compliance", "gdpr", "hipaa", "sox", "regulation",
        "legal", "lawyer", "attorney", "counsel",
        "privacy", "data protection", "data handling", "pii",
        "intellectual property", "ip", "patent", "trademark", "copyright",
        "vendor agreement", "terms of service", "tos", "eula",
        "audit", "regulatory", "sanctions", "export control",
        "code of conduct", "ethics", "whistleblower", "conflict of interest",
        "liability", "indemnification", "clause", "redline",
    ]
    examples = [
        "Can you review this NDA?",
        "What's our data retention policy under GDPR?",
        "Is this vendor contract standard or are there unusual clauses?",
        "Do I need approval to share data with a third party?",
        "What's the process for reporting a compliance concern?",
        "Can I use open-source code in our product?",
    ]

    def can_handle(self, text: str) -> float:
        score = self._keyword_score(text)
        low = text.lower()
        if any(p in low for p in ["review contract", "nda", "compliance", "gdpr", "legal question"]):
            score = max(score, 0.8)
        if any(p in low for p in ["data privacy", "terms of service", "intellectual property"]):
            score = max(score, 0.6)
        return score

    async def execute(self, context: SkillContext, request: str) -> SkillResult:
        system = SYSTEM_PROMPT.format(
            user_name=context.user_name,
            user_role=context.user_role or "Employee",
        )

        response = await self._ask_ollama(system, request)

        # Always append legal disclaimer
        response += (
            "\n\n_Note: This is general guidance, not legal advice. "
            "For binding legal matters, please consult with the legal department._"
        )

        return SkillResult(
            success=True,
            message=response,
        )
