"""Background learning engine that analyzes feedback and generates improvement suggestions."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

from app.config import settings
from app.services.ollama import OllamaClient
from app.storage.database import Database

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are analyzing user feedback on an AI assistant's responses.
Below are question-answer pairs that users rated negatively (thumbs down).

For each pair, suggest how the assistant's system prompt could be improved to give better answers in the future. Be specific and concise.

Return a single paragraph describing the system prompt improvement. Do NOT repeat the questions or answers — just describe the change.

---
{qa_pairs}
---

System prompt improvement suggestion:"""


class LearningEngine:
    """Analyzes feedback and generates learning entries for admin review."""

    def __init__(self, database: Database, ollama: OllamaClient):
        self.db = database
        self.ollama = ollama
        self._last_run: str = ""  # ISO timestamp of last successful run

    async def run_cycle(self) -> dict:
        """Run one learning cycle. Returns summary of what was generated."""
        since = self._last_run or (datetime.utcnow() - timedelta(hours=settings.learning_interval_hours)).isoformat()
        self._last_run = datetime.utcnow().isoformat()

        summary = {
            "prompt_refinements": 0,
            "response_improvements": 0,
            "knowledge_expansions": 0,
        }

        # 1. Analyze negative feedback
        negatives = await self.db.get_negative_feedback_since(since)
        if negatives:
            await self._process_negative_feedback(negatives, summary)

        # 2. Find knowledge expansion candidates from positive feedback
        positives = await self.db.get_positive_feedback_since(since)
        if positives:
            await self._process_positive_feedback(positives, summary)

        # 3. Generate daily satisfaction snapshot
        await self._snapshot_satisfaction()

        total = sum(summary.values())
        if total:
            logger.info("Learning cycle complete: %s", summary)
        else:
            logger.debug("Learning cycle complete: no new entries generated")

        return summary

    async def _process_negative_feedback(self, feedback_list: list[dict], summary: dict) -> None:
        """Group negative feedback by source and generate learning entries."""
        local_feedback = [f for f in feedback_list if f.get("source") == "local"]
        nexgai_feedback = [f for f in feedback_list if f.get("source") == "nexgai"]

        # Local LLM: generate prompt refinement suggestions
        if len(local_feedback) >= settings.learning_min_negative_feedback:
            await self._suggest_prompt_refinement(local_feedback, summary)

        # NexgAI: group by agent and flag for admin review
        agents: dict[str, list[dict]] = {}
        for f in nexgai_feedback:
            agent = f.get("agent_name") or "unknown"
            agents.setdefault(agent, []).append(f)

        for agent_name, agent_feedback in agents.items():
            if len(agent_feedback) < 2:
                continue
            qa = "\n".join(
                f"Q: {f.get('user_query', '?')}\nA: {f.get('message_content', '?')[:300]}"
                for f in agent_feedback[:5]
            )
            await self.db.add_learning_entry({
                "id": str(uuid.uuid4()),
                "entry_type": "response_improvement",
                "source": "nexgai",
                "agent_name": agent_name,
                "trigger_feedback_ids": json.dumps([f["id"] for f in agent_feedback]),
                "original_query": qa[:500],
                "original_response": f"{len(agent_feedback)} negative feedback items for agent {agent_name}",
                "suggested_improvement": f"Review {agent_name} agent configuration in Agent Hub — users rated {len(agent_feedback)} responses negatively.",
            })
            summary["response_improvements"] += 1

    async def _suggest_prompt_refinement(self, feedback_list: list[dict], summary: dict) -> None:
        """Use Ollama to suggest system prompt improvements based on negative feedback."""
        qa_pairs = "\n\n".join(
            f"Q: {f.get('user_query', '(unknown)')}\nA: {f.get('message_content', '(unknown)')[:300]}"
            + (f"\nUser comment: {f['comment']}" if f.get("comment") else "")
            for f in feedback_list[:10]
        )

        prompt = ANALYSIS_PROMPT.format(qa_pairs=qa_pairs)

        try:
            result = await self.ollama.chat(messages=[
                {"role": "system", "content": "You are an AI prompt engineer."},
                {"role": "user", "content": prompt},
            ])
            suggestion = result.get("message", {}).get("content", "").strip()
            if not suggestion:
                return

            await self.db.add_learning_entry({
                "id": str(uuid.uuid4()),
                "entry_type": "prompt_refinement",
                "source": "local",
                "trigger_feedback_ids": json.dumps([f["id"] for f in feedback_list]),
                "original_query": qa_pairs[:500],
                "original_response": f"{len(feedback_list)} negatively-rated local LLM responses",
                "suggested_improvement": suggestion,
            })
            summary["prompt_refinements"] += 1

        except Exception as e:
            logger.error("Prompt refinement generation failed: %s", e)

    async def _process_positive_feedback(self, feedback_list: list[dict], summary: dict) -> None:
        """Identify highly-rated local LLM responses as knowledge expansion candidates."""
        for f in feedback_list:
            query = f.get("user_query", "")
            response = f.get("message_content", "")
            if not query or not response or len(response) < 50:
                continue

            await self.db.add_learning_entry({
                "id": str(uuid.uuid4()),
                "entry_type": "knowledge_expansion",
                "source": "local",
                "trigger_feedback_ids": json.dumps([f["id"]]),
                "original_query": query[:500],
                "original_response": response[:2000],
                "suggested_improvement": f"Add to knowledge base — user rated this response positively:\n\nQ: {query}\nA: {response[:500]}",
            })
            summary["knowledge_expansions"] += 1

    async def _snapshot_satisfaction(self) -> None:
        """Generate daily satisfaction snapshot."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        for source in ("local", "nexgai", "all"):
            src_filter = source if source != "all" else None
            stats = await self.db.get_feedback_stats(period_hours=24, source=src_filter)
            if stats["total"] > 0:
                await self.db.save_satisfaction_snapshot(today, source, stats)
