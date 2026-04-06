"""Multi-agent orchestrator for MyAi — breaks complex tasks into parallel subtasks."""
from __future__ import annotations
import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class SubTask:
    def __init__(self, description: str, tool: str = "", args: dict = None):
        self.description = description
        self.tool = tool
        self.args = args or {}
        self.result: str = ""
        self.status: str = "pending"
        self.elapsed: float = 0


class CrewOrchestrator:
    """Breaks complex tasks into subtasks and executes them in parallel."""

    def __init__(self, tool_registry, ollama_client):
        self.tools = tool_registry
        self.ollama = ollama_client

    async def decompose_task(self, task: str) -> list[SubTask]:
        """Use LLM to break a complex task into subtasks."""
        prompt = f"""Break this task into 2-5 simple subtasks that can be done independently.
For each subtask, specify which tool to use.

Available tools: read_file, list_directory, search_files, write_file, web_search, system_info, git_status, url_summarizer

Task: {task}

Reply in this exact JSON format (nothing else):
[
  {{"description": "what to do", "tool": "tool_name", "args": {{"key": "value"}}}},
  ...
]"""

        try:
            result = await self.ollama.chat(messages=[
                {"role": "system", "content": "You decompose tasks into subtasks. Reply ONLY with JSON array."},
                {"role": "user", "content": prompt},
            ])
            content = result.get("message", {}).get("content", "").strip()

            import json, re
            # Extract JSON from response
            json_match = re.search(r'\[[\s\S]*\]', content)
            if json_match:
                subtasks_data = json.loads(json_match.group())
                return [SubTask(**st) for st in subtasks_data]
        except Exception as e:
            logger.warning(f"Task decomposition failed: {e}")

        # Fallback: single task
        return [SubTask(description=task)]

    async def execute_subtask(self, subtask: SubTask) -> SubTask:
        """Execute a single subtask."""
        t0 = time.time()
        try:
            if subtask.tool and subtask.tool in self.tools._tools:
                subtask.result = await self.tools.execute(subtask.tool, subtask.args)
            else:
                # Use LLM for tasks without specific tools
                result = await self.ollama.chat(messages=[
                    {"role": "user", "content": subtask.description},
                ])
                subtask.result = result.get("message", {}).get("content", "").strip()
            subtask.status = "done"
        except Exception as e:
            subtask.result = f"Error: {e}"
            subtask.status = "failed"
        subtask.elapsed = time.time() - t0
        return subtask

    async def orchestrate(self, task: str) -> str:
        """Break task into subtasks, execute in parallel, merge results."""
        logger.info(f"Orchestrating: {task}")

        # Step 1: Decompose
        subtasks = await self.decompose_task(task)
        logger.info(f"Decomposed into {len(subtasks)} subtasks")

        # Step 2: Execute in parallel
        results = await asyncio.gather(
            *[self.execute_subtask(st) for st in subtasks],
            return_exceptions=True,
        )

        # Step 3: Merge results
        parts = []
        for st in subtasks:
            status_icon = "done" if st.status == "done" else "failed"
            parts.append(f"[{status_icon}] {st.description} ({st.elapsed:.1f}s)\n{st.result}")

        merged = "\n\n".join(parts)

        # Step 4: Use LLM to summarize
        try:
            summary_result = await self.ollama.chat(messages=[
                {"role": "system", "content": "Summarize these results into a clear, concise response for the user. Do not mention tools or subtasks."},
                {"role": "user", "content": f"Original request: {task}\n\nResults:\n{merged}"},
            ])
            return summary_result.get("message", {}).get("content", merged).strip()
        except Exception:
            return merged
