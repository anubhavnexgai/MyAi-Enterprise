"""Planner — LLM-driven goal decomposition into a step list.

Used by AutonomyService to turn a high-level goal ("draft a status report and
email it to Priti") into an ordered list of concrete tool invocations.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.services.ollama import OllamaClient
from app.services.policy import get_policy

logger = logging.getLogger(__name__)


@dataclass
class Step:
    description: str
    tool: str = ""              # empty = "use the LLM to think, no tool"
    args: dict | None = None
    success_criteria: str = ""  # natural-language condition to verify after running


PLANNER_SYSTEM = """You are the planner for an autonomous AI assistant. You
decompose a user's goal into a small list of concrete steps the assistant can
actually execute with its tools.

RULES:
- Output ONLY valid JSON. No prose before or after.
- Each step must be executable with one tool call OR one LLM thought.
- Prefer 2-6 steps. Don't over-decompose trivial things.
- Use only tools from the provided list. Don't invent tools.
- For thinking/synthesis steps (e.g. "draft a paragraph"), set tool to "" and
  put the prompt in description.
- success_criteria is a one-line natural language check: "the file exists",
  "an email draft is open", etc.

OUTPUT FORMAT:
{
  "steps": [
    {"description": "...", "tool": "tool_name_or_empty", "args": {...}, "success_criteria": "..."}
  ]
}
"""


class Planner:
    def __init__(self, ollama: OllamaClient | None = None):
        self.ollama = ollama or OllamaClient()

    async def plan(
        self,
        goal: str,
        persona: str,
        available_tools: list[str],
    ) -> list[Step]:
        # Allow the policy to direct planning to a dedicated model role.
        model_override = get_policy().model_for("planner")

        prompt = (
            f"Persona: {persona}\n"
            f"Goal: {goal}\n\n"
            f"Available tools: {', '.join(available_tools)}\n\n"
            "Produce the step plan."
        )
        result = await self.ollama.chat(messages=[
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": prompt},
        ])
        content = result.get("message", {}).get("content", "").strip()
        return self._parse(content)

    async def replan(
        self,
        goal: str,
        persona: str,
        available_tools: list[str],
        completed: list[Step],
        failed: Step,
        failure_reason: str,
    ) -> list[Step]:
        completed_summary = "\n".join(
            f"- DONE: {s.description}" for s in completed
        ) or "(none yet)"
        prompt = (
            f"Persona: {persona}\n"
            f"Goal: {goal}\n\n"
            f"Available tools: {', '.join(available_tools)}\n\n"
            f"Completed so far:\n{completed_summary}\n\n"
            f"FAILED step: {failed.description}\n"
            f"Failure reason: {failure_reason}\n\n"
            "Produce a NEW plan for the remaining work. Do not include the "
            "completed steps. Try a different approach for the failed step "
            "or skip it if there is a workaround."
        )
        result = await self.ollama.chat(messages=[
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": prompt},
        ])
        return self._parse(result.get("message", {}).get("content", "").strip())

    @staticmethod
    def _parse(content: str) -> list[Step]:
        # Pull the first JSON object out of the response — be tolerant of
        # leading/trailing prose if the model misbehaves.
        m = re.search(r"\{[\s\S]*\}", content)
        if not m:
            logger.warning("Planner: no JSON found in response: %s", content[:200])
            return []
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError as exc:
            logger.warning("Planner: JSON parse failed: %s", exc)
            return []
        steps = []
        for raw in data.get("steps", []):
            steps.append(Step(
                description=str(raw.get("description", "")).strip(),
                tool=str(raw.get("tool", "")).strip(),
                args=raw.get("args") if isinstance(raw.get("args"), dict) else {},
                success_criteria=str(raw.get("success_criteria", "")).strip(),
            ))
        return [s for s in steps if s.description]
