"""JANUS — AI Project Coordinator.

Handles: project status, task tracking, sprint planning, standup summaries,
risk identification, cross-team coordination.
"""

from __future__ import annotations

from app.skills.base import BaseSkill, SkillContext, SkillResult

SYSTEM_PROMPT = """You are JANUS, an AI Project Coordinator within the enterprise.
You help employees with project management tasks including:
- Project status updates and summaries
- Task creation, assignment, and tracking
- Sprint planning and backlog grooming
- Standup notes and synthesis
- Risk identification and mitigation planning
- Cross-team dependency tracking
- Milestone tracking and deadline management
- Resource allocation questions
- Retrospective facilitation
- Project documentation

You are speaking to: {user_name} ({user_role})

Rules:
- Keep status updates concise and actionable
- Highlight blockers and risks prominently
- Use structured formats (bullet points, tables) for clarity
- For sprint planning, consider team velocity and capacity
- Flag overdue items and approaching deadlines
- Suggest process improvements when you notice patterns
- Cross-reference with the user's project context when available"""


class ProjectCoordinationSkill(BaseSkill):
    name = "project_coordination"
    agent_name = "JANUS"
    description = "Project management: status, tasks, sprints, risks, coordination"
    keywords = [
        "project", "task", "sprint", "backlog", "ticket",
        "status update", "progress", "milestone", "deadline",
        "standup", "stand-up", "daily sync", "weekly sync",
        "blocker", "blocked", "dependency", "risk",
        "jira", "asana", "trello", "linear", "monday",
        "roadmap", "timeline", "gantt", "estimate",
        "retrospective", "retro", "velocity", "story points",
        "epic", "user story", "acceptance criteria",
        "resource", "allocation", "bandwidth", "capacity",
    ]
    examples = [
        "What's the status of the API migration project?",
        "Create tasks for the new feature rollout",
        "Summarize this week's standup notes",
        "What are the blockers for the Q2 release?",
        "Help me plan the next sprint",
        "Write acceptance criteria for the login feature",
    ]

    def can_handle(self, text: str) -> float:
        score = self._keyword_score(text)
        low = text.lower()
        if any(p in low for p in [
            "project", "sprint", "standup", "blocker", "backlog", "milestone",
            "task", "jira", "asana", "trello",
        ]):
            score = max(score, 0.6)
        if any(p in low for p in ["sprint plan", "project status", "create task"]):
            score = max(score, 0.8)
        return min(score, 1.0)

    async def execute(self, context: SkillContext, request: str) -> SkillResult:
        # Load project contexts for awareness
        project_context = ""
        if self.database:
            contexts = await self.database.get_all_contexts(context.user_id)
            if contexts:
                project_context = "\n## User's Project Context\n"
                for ctx in contexts:
                    project_context += f"### {ctx['name']}\n{ctx['content'][:500]}\n\n"

        system = SYSTEM_PROMPT.format(
            user_name=context.user_name,
            user_role=context.user_role or "Team Member",
        ) + project_context

        response = await self._ask_ollama(system, request)

        return SkillResult(
            success=True,
            message=response,
        )
