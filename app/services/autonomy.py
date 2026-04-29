"""AutonomyService — runs goal plans to completion with replan-on-failure.

Persists goals + steps in `data/governance.db` so they survive restarts.
Runs steps sequentially (parallel-by-deps is a v2 enhancement). On a step
failure, asks the planner once for a revised plan covering the remaining
work; if that also fails, the goal is marked failed.

Tools that need approval are awaited (wait_for_approval=True) — autonomy
is OK with the user being slow to ✅, that's the whole point of the
governance plane.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.ollama import OllamaClient
from app.services.planner import Planner, Step

if TYPE_CHECKING:
    from app.agent.tools import ToolRegistry

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    goal TEXT NOT NULL,
    persona TEXT,
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed | cancelled
    completed_at TEXT,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    description TEXT NOT NULL,
    tool TEXT,
    args TEXT,                              -- JSON
    success_criteria TEXT,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | running | done | failed | skipped
    result TEXT,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (goal_id) REFERENCES goals(id)
);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_steps_goal ON steps(goal_id);
"""

MAX_REPLANS = 1
APPROVAL_TIMEOUT_S = 600.0


class AutonomyService:
    def __init__(
        self,
        tools: ToolRegistry,
        ollama: OllamaClient | None = None,
        db_path: Path | str | None = None,
    ):
        self.tools = tools
        self.ollama = ollama or OllamaClient()
        self.planner = Planner(self.ollama)
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "governance.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        # Track running goal ids -> asyncio.Task so cancel() works
        self._tasks: dict[int, asyncio.Task] = {}

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ---- public API --------------------------------------------------------

    async def start(
        self,
        goal: str,
        persona: str = "default",
        requested_by: str = "user",
    ) -> int:
        """Plan + kick off the goal in the background. Returns goal_id."""
        # Insert goal row
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO goals (ts, goal, persona, requested_by, status) "
                "VALUES (?,?,?,?, 'pending')",
                (datetime.now().isoformat(timespec="milliseconds"), goal, persona, requested_by),
            )
            conn.commit()
            goal_id = int(cur.lastrowid)

        # Plan synchronously so the caller knows whether planning succeeded
        available_tools = list(self.tools._tools.keys())
        steps = await self.planner.plan(goal, persona, available_tools)
        if not steps:
            self._mark_goal(goal_id, "failed", summary="Planner returned no steps.")
            return goal_id

        self._save_steps(goal_id, steps)

        # Kick off execution
        task = asyncio.create_task(self._run(goal_id, persona, goal))
        self._tasks[goal_id] = task
        return goal_id

    def status(self, goal_id: int) -> dict:
        with self._connect() as conn:
            g = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if g is None:
                return {"error": f"goal {goal_id} not found"}
            steps = conn.execute(
                "SELECT * FROM steps WHERE goal_id = ? ORDER BY idx ASC", (goal_id,)
            ).fetchall()
        return {
            "goal": dict(g),
            "steps": [dict(s) for s in steps],
        }

    def cancel(self, goal_id: int) -> bool:
        task = self._tasks.get(goal_id)
        if task is not None and not task.done():
            task.cancel()
        self._mark_goal(goal_id, "cancelled", summary="Cancelled by user.")
        return True

    def list_goals(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM goals ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- run loop ----------------------------------------------------------

    async def _run(self, goal_id: int, persona: str, goal: str) -> None:
        self._mark_goal(goal_id, "running")
        replans_remaining = MAX_REPLANS
        try:
            while True:
                pending = self._next_pending_steps(goal_id)
                if not pending:
                    break  # all steps done

                step_row = pending[0]  # sequential: one at a time
                self._mark_step(step_row["id"], status="running",
                                started_at=datetime.now().isoformat())
                step = Step(
                    description=step_row["description"],
                    tool=step_row["tool"] or "",
                    args=json.loads(step_row["args"] or "{}"),
                    success_criteria=step_row["success_criteria"] or "",
                )
                logger.info("Autonomy goal=%s step=%s: %s", goal_id, step_row["idx"], step.description)

                ok, result = await self._run_step(step, persona)
                self._mark_step(
                    step_row["id"],
                    status="done" if ok else "failed",
                    result=str(result)[:4000],
                    completed_at=datetime.now().isoformat(),
                )

                if not ok and replans_remaining > 0:
                    replans_remaining -= 1
                    completed = [
                        Step(description=s["description"], tool=s["tool"] or "",
                             args=json.loads(s["args"] or "{}"))
                        for s in self._steps_with_status(goal_id, "done")
                    ]
                    new_steps = await self.planner.replan(
                        goal=goal,
                        persona=persona,
                        available_tools=list(self.tools._tools.keys()),
                        completed=completed,
                        failed=step,
                        failure_reason=str(result)[:500],
                    )
                    if new_steps:
                        # Mark remaining pending+failed steps as skipped, append the new plan
                        self._skip_remaining(goal_id)
                        self._save_steps(goal_id, new_steps, append=True)
                        continue
                if not ok:
                    self._mark_goal(goal_id, "failed",
                                    summary=f"Step '{step.description}' failed: {result}")
                    return

            self._mark_goal(goal_id, "done", summary=self._summarise(goal_id))
        except asyncio.CancelledError:
            self._mark_goal(goal_id, "cancelled", summary="Cancelled.")
        except Exception as exc:
            logger.error("Autonomy goal=%s crashed: %s", goal_id, exc, exc_info=True)
            self._mark_goal(goal_id, "failed", summary=f"Internal error: {exc}")
        finally:
            self._tasks.pop(goal_id, None)

    async def _run_step(self, step: Step, persona: str) -> tuple[bool, str]:
        """Execute one step. Return (success, result_text)."""
        if step.tool:
            try:
                result = await self.tools.execute(
                    step.tool,
                    step.args or {},
                    persona=persona,
                    actor="autonomy",
                    wait_for_approval=True,
                    approval_timeout=APPROVAL_TIMEOUT_S,
                )
            except Exception as e:
                return False, f"tool error: {e}"
            # Heuristic: if the tool returned a "blocked" / "rejected" / error string,
            # treat as failure so replan can try something else.
            if any(marker in result for marker in ("Action blocked", "❌", "⛔", "rejected", "blocked")):
                return False, result
            return True, result
        # No tool — use the LLM as a "thinking" step.
        try:
            llm = await self.ollama.chat(messages=[
                {"role": "user", "content": step.description},
            ])
            return True, llm.get("message", {}).get("content", "").strip()
        except Exception as e:
            return False, f"llm error: {e}"

    # ---- DB helpers --------------------------------------------------------

    def _mark_goal(self, goal_id: int, status: str, summary: str = "") -> None:
        completed = datetime.now().isoformat() if status in ("done", "failed", "cancelled") else None
        with self._lock, self._connect() as conn:
            if completed:
                conn.execute(
                    "UPDATE goals SET status=?, summary=?, completed_at=? WHERE id=?",
                    (status, summary or None, completed, goal_id),
                )
            else:
                conn.execute("UPDATE goals SET status=?, summary=? WHERE id=?",
                             (status, summary or None, goal_id))
            conn.commit()

    def _mark_step(self, step_id: int, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        params = list(fields.values()) + [step_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE steps SET {sets} WHERE id=?", params)
            conn.commit()

    def _save_steps(self, goal_id: int, steps: list[Step], append: bool = False) -> None:
        with self._lock, self._connect() as conn:
            base_idx = 0
            if append:
                row = conn.execute(
                    "SELECT COALESCE(MAX(idx), -1) AS m FROM steps WHERE goal_id = ?", (goal_id,)
                ).fetchone()
                base_idx = (row["m"] or -1) + 1
            for i, st in enumerate(steps):
                conn.execute(
                    "INSERT INTO steps (goal_id, idx, description, tool, args, success_criteria) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        goal_id,
                        base_idx + i,
                        st.description,
                        st.tool or None,
                        json.dumps(st.args or {}, ensure_ascii=False, default=str),
                        st.success_criteria or None,
                    ),
                )
            conn.commit()

    def _next_pending_steps(self, goal_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE goal_id = ? AND status='pending' ORDER BY idx ASC",
                (goal_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def _steps_with_status(self, goal_id: int, status: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE goal_id = ? AND status = ? ORDER BY idx ASC",
                (goal_id, status),
            ).fetchall()
            return [dict(r) for r in rows]

    def _skip_remaining(self, goal_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE steps SET status='skipped' WHERE goal_id=? AND status IN ('pending','failed')",
                (goal_id,),
            )
            conn.commit()

    def _summarise(self, goal_id: int) -> str:
        with self._connect() as conn:
            done = conn.execute(
                "SELECT COUNT(*) FROM steps WHERE goal_id=? AND status='done'", (goal_id,)
            ).fetchone()[0]
            total = conn.execute(
                "SELECT COUNT(*) FROM steps WHERE goal_id=?", (goal_id,)
            ).fetchone()[0]
        return f"Completed {done}/{total} steps."


_singleton: AutonomyService | None = None


def get_autonomy(tools=None) -> AutonomyService:
    """Get or initialise the singleton. Tools must be passed on first call."""
    global _singleton
    if _singleton is None:
        if tools is None:
            raise RuntimeError(
                "AutonomyService not yet initialised — pass `tools=` on first call"
            )
        _singleton = AutonomyService(tools=tools)
    return _singleton
