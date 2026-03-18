"""MIDAS — AI Finance Operations Manager.

Handles: expense reports, invoice queries, budget questions, financial reporting,
reimbursements, purchase orders, vendor payments.
"""

from __future__ import annotations

from app.skills.base import BaseSkill, SkillContext, SkillResult

SYSTEM_PROMPT = """You are MIDAS, an AI Finance Operations Manager within the enterprise.
You help employees with finance and accounting inquiries including:
- Expense report submission and status
- Invoice processing and payment status
- Budget inquiries and allocation questions
- Purchase order requests and approvals
- Reimbursement requests and policies
- Vendor payment inquiries
- Financial reporting and analytics
- Tax-related questions (withholding, forms)
- Corporate card management
- Travel and entertainment policy

You are speaking to: {user_name} ({user_role})

Rules:
- Be precise with numbers and dates
- For expense submissions, collect: amount, category, date, business purpose, receipt status
- For purchase requests, note the approval chain required
- Always mention if something needs finance team or manager approval
- Reference company expense policies when applicable
- Never disclose other employees' financial information
- For tax advice, recommend consulting a tax professional"""


class FinanceSkill(BaseSkill):
    name = "finance"
    agent_name = "MIDAS"
    description = "Finance ops: expenses, invoices, budgets, reimbursements, POs"
    keywords = [
        "expense", "expense report", "receipt", "reimbursement", "reimburse",
        "invoice", "payment", "pay", "vendor", "purchase order", "po",
        "budget", "cost center", "allocation", "forecast",
        "corporate card", "company card", "credit card",
        "travel expense", "mileage", "per diem",
        "accounts payable", "accounts receivable", "ap", "ar",
        "financial report", "p&l", "profit", "revenue", "cost",
        "tax", "withholding", "1099", "w9",
        "procurement", "spend", "approve purchase",
    ]
    examples = [
        "I need to submit an expense report for my client dinner",
        "What's the status of invoice #12345?",
        "How much budget is left in the engineering cost center?",
        "I need to request a purchase order for new monitors",
        "What's the reimbursement policy for travel?",
        "When will my expense report be processed?",
    ]

    def can_handle(self, text: str) -> float:
        score = self._keyword_score(text)
        low = text.lower()
        if any(p in low for p in ["expense report", "submit expense", "reimbursement", "purchase order"]):
            score = max(score, 0.85)
        if any(p in low for p in ["invoice", "budget", "payment status", "corporate card"]):
            score = max(score, 0.6)
        return score

    async def execute(self, context: SkillContext, request: str) -> SkillResult:
        system = SYSTEM_PROMPT.format(
            user_name=context.user_name,
            user_role=context.user_role or "Employee",
        )

        response = await self._ask_ollama(system, request)

        needs_approval = any(
            kw in request.lower()
            for kw in ["purchase order", "expense report", "reimbursement", "approve", "procurement"]
        )

        return SkillResult(
            success=True,
            message=response,
            needs_approval=needs_approval,
            approval_prompt="This requires finance/manager approval." if needs_approval else "",
        )
