"""EKLAVYA — AI Executive Assistant.

Handles: scheduling, meeting prep, email drafting, task management,
daily briefings, travel coordination, document preparation.

When connected to Microsoft 365 via Graph, EKLAVYA can access real
calendar events, emails, files, and people data.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.skills.base import BaseSkill, SkillContext, SkillResult

if TYPE_CHECKING:
    from app.services.graph import GraphClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are EKLAVYA, an AI Executive Assistant within the enterprise.
You help employees with productivity and administrative tasks including:
- Meeting scheduling and calendar management
- Meeting preparation (agendas, briefing docs, attendee research)
- Email drafting and reply suggestions
- Task tracking and prioritization
- Daily/weekly briefings and summaries
- Travel arrangement coordination
- Document preparation and formatting
- Expense report assistance
- Contact and relationship management
- Note-taking and action item tracking

You are speaking to: {user_name} ({user_role})

{meeting_context}

{graph_context}

Rules:
- Be proactive and anticipatory — suggest what the user might need next
- For scheduling, consider time zones, working hours, and meeting load
- Draft emails in the user's professional voice
- For meeting prep, include key talking points and relevant context
- Prioritize tasks by urgency and importance
- Keep briefings concise and actionable
- When you have real calendar/email data, reference it specifically
- If calendar/email access isn't available yet, suggest using /connect"""


class ExecutiveAssistantSkill(BaseSkill):
    name = "executive_assistant"
    agent_name = "EKLAVYA"
    description = "Executive support: scheduling, email, meetings, tasks, briefings"
    keywords = [
        "schedule", "meeting", "calendar", "appointment", "book a",
        "email", "draft email", "reply to", "respond to", "write an email",
        "task", "todo", "to-do", "remind me", "reminder", "deadline",
        "briefing", "summary", "daily brief", "weekly brief",
        "travel", "flight", "hotel", "itinerary",
        "agenda", "meeting prep", "talking points", "meeting notes",
        "prioritize", "priorities", "what should i focus on",
        "follow up", "follow-up", "action items",
        "reschedule", "cancel meeting", "move meeting",
        "free time", "availability", "when am i free",
    ]
    examples = [
        "Schedule a meeting with the design team this Thursday",
        "Draft an email to the client about the project delay",
        "What's on my calendar tomorrow?",
        "Prepare a briefing for my 2pm meeting",
        "Help me prioritize my tasks for this week",
        "Write a follow-up email after today's standup",
    ]

    def __init__(self, ollama=None, database=None, platform_url: str = "",
                 graph_client: GraphClient | None = None):
        super().__init__(ollama=ollama, database=database, platform_url=platform_url)
        self.graph_client = graph_client

    def can_handle(self, text: str) -> float:
        score = self._keyword_score(text)
        low = text.lower()
        if any(p in low for p in ["schedule a meeting", "draft email", "meeting prep", "daily brief"]):
            score = max(score, 0.85)
        if any(p in low for p in ["calendar", "remind me", "action items", "prioritize"]):
            score = max(score, 0.55)
        return score

    async def _get_graph_context(self, user_id: str, request: str) -> str:
        """Fetch relevant Graph data based on the request."""
        if not self.graph_client or not self.graph_client.is_user_connected(user_id):
            return ""

        context_parts = []
        low = request.lower()

        # Fetch calendar if request mentions meetings/calendar/schedule
        if any(kw in low for kw in [
            "calendar", "meeting", "schedule", "agenda", "briefing",
            "free", "availability", "tomorrow", "today", "this week",
        ]):
            try:
                events = await self.graph_client.get_calendar_events(user_id, top=10, days_ahead=7)
                if events:
                    context_parts.append("## Live Calendar (Next 7 Days)")
                    for e in events:
                        start = e["start"][:16].replace("T", " ") if e["start"] else "?"
                        end = e["end"][:16].replace("T", " ") if e["end"] else "?"
                        attendees = ", ".join(e.get("attendees", [])[:5])
                        context_parts.append(
                            f"- {start} to {end}: {e['subject']}"
                            f"{' | ' + e['location'] if e.get('location') else ''}"
                            f"{' | Attendees: ' + attendees if attendees else ''}"
                        )
                    context_parts.append("")
            except Exception as e:
                logger.warning(f"Failed to fetch calendar for EKLAVYA: {e}")

        # Fetch emails if request mentions email/inbox/messages
        if any(kw in low for kw in [
            "email", "inbox", "mail", "message", "reply", "draft", "send",
            "unread", "follow up", "respond",
        ]):
            try:
                emails = await self.graph_client.get_recent_emails(user_id, top=5)
                if emails:
                    context_parts.append("## Recent Emails")
                    for e in emails:
                        read = "" if e["is_read"] else " [UNREAD]"
                        context_parts.append(
                            f"- {e['received'][:16]} | From: {e['from']} | "
                            f"Subject: {e['subject']}{read}\n  Preview: {e['preview'][:120]}"
                        )
                    context_parts.append("")
            except Exception as e:
                logger.warning(f"Failed to fetch emails for EKLAVYA: {e}")

        # Fetch people if request mentions contacts/people
        if any(kw in low for kw in ["who", "people", "contact", "colleague", "team"]):
            try:
                people = await self.graph_client.get_people(user_id, top=5)
                if people:
                    context_parts.append("## Frequent Contacts")
                    for p in people:
                        context_parts.append(
                            f"- {p['name']} ({p.get('title', '')}) — {p.get('email', '')}"
                        )
                    context_parts.append("")
            except Exception as e:
                logger.warning(f"Failed to fetch people for EKLAVYA: {e}")

        if context_parts:
            return "\n".join(context_parts)
        return ""

    async def execute(self, context: SkillContext, request: str) -> SkillResult:
        # Build meeting context from recent meetings
        meeting_context = ""
        if self.database:
            recent = await self.database.get_recent_meetings(context.user_id, limit=3)
            if recent:
                meeting_context = "## Recent Meeting History\n"
                for m in recent:
                    meeting_context += f"- {m['meeting_subject'] or 'Untitled'}"
                    if m.get("key_points"):
                        meeting_context += f": {m['key_points']}"
                    meeting_context += "\n"

            # Load user contexts for project awareness
            contexts = await self.database.get_all_contexts(context.user_id)
            if contexts:
                meeting_context += "\n## User's Active Projects/Context\n"
                for ctx in contexts[:5]:
                    meeting_context += f"### {ctx['name']}\n{ctx['content'][:300]}\n\n"

        # Fetch live Graph data
        graph_context = await self._get_graph_context(context.user_id, request)
        if not graph_context and self.graph_client and self.graph_client.is_configured:
            if not self.graph_client.is_user_connected(context.user_id):
                graph_context = (
                    "Note: User has NOT connected their Microsoft 365 account. "
                    "Suggest they use /connect to enable real calendar and email access."
                )

        system = SYSTEM_PROMPT.format(
            user_name=context.user_name,
            user_role=context.user_role or "Employee",
            meeting_context=meeting_context,
            graph_context=graph_context,
        )

        response = await self._ask_ollama(system, request)

        return SkillResult(
            success=True,
            message=response,
        )
