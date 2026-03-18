"""VULCAN — AI IT Support Specialist.

Handles: password resets, software provisioning, VPN/connectivity issues,
access management, troubleshooting, IT knowledge base queries.
"""

from __future__ import annotations

from app.skills.base import BaseSkill, SkillContext, SkillResult

SYSTEM_PROMPT = """You are VULCAN, an AI IT Support Specialist within the enterprise.
You help employees with IT issues including:
- Password resets and MFA/2FA problems
- VPN, Wi-Fi, and connectivity troubleshooting
- Software installation, licensing, and access requests
- Email and calendar configuration issues
- Printer and hardware troubleshooting
- Security incident reporting guidance
- IT policy questions

You are speaking to: {user_name} ({user_role})

Rules:
- Be helpful, clear, and step-by-step in your troubleshooting
- If a password reset or access change is needed, explain what you'd do and note it requires IT admin approval
- For security incidents, treat them with urgency
- If you can solve it with instructions, provide complete steps
- If it requires manual IT intervention, say so clearly and offer to create a ticket
- Keep responses professional but approachable"""


class ITSupportSkill(BaseSkill):
    name = "it_support"
    agent_name = "VULCAN"
    description = "IT helpdesk: passwords, VPN, software, access, troubleshooting"
    keywords = [
        "password", "reset password", "vpn", "wifi", "wi-fi", "internet",
        "can't connect", "cannot connect", "login", "log in", "locked out",
        "access", "permission", "software", "install", "license",
        "printer", "print", "email not working", "outlook", "mfa", "2fa",
        "two factor", "authenticator", "laptop", "computer", "slow",
        "it support", "it help", "helpdesk", "help desk", "it ticket",
        "screen", "monitor", "keyboard", "mouse", "bluetooth",
        "teams not working", "slack not working", "zoom not working",
        "onedrive", "sharepoint", "drive full", "storage", "backup",
    ]
    examples = [
        "I forgot my password",
        "My VPN isn't connecting",
        "I need access to the finance SharePoint",
        "How do I install Python on my work laptop?",
        "My laptop is running really slow",
        "I can't connect to the printer",
    ]

    def can_handle(self, text: str) -> float:
        score = self._keyword_score(text)
        low = text.lower()
        # Boost for clear IT requests
        if any(p in low for p in [
            "password", "vpn", "locked out", "it ticket", "can't login", "cannot login",
            "reset my", "help desk", "it support",
        ]):
            score = max(score, 0.7)
        if any(p in low for p in ["install", "access to", "not working", "connectivity"]):
            score = max(score, 0.5)
        return min(score, 1.0)

    async def execute(self, context: SkillContext, request: str) -> SkillResult:
        system = SYSTEM_PROMPT.format(
            user_name=context.user_name,
            user_role=context.user_role or "Employee",
        )

        response = await self._ask_ollama(system, request)

        # Detect if this needs IT admin action
        needs_approval = any(
            kw in request.lower()
            for kw in ["reset password", "access to", "install", "license", "provision"]
        )

        return SkillResult(
            success=True,
            message=response,
            needs_approval=needs_approval,
            approval_prompt="This action may require IT admin approval." if needs_approval else "",
        )
