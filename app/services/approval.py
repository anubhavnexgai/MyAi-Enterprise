"""ApprovalService — pending-actions queue with async wait.

When the policy says a tool needs approval, the ToolRegistry calls
`approval.queue(...)` instead of running it. The queued action sits in
`data/governance.db :: pending_actions` until:

  1. A human approves/rejects via:
       - The web admin UI (planned)
       - A WhatsApp/Telegram reply (planned, Pillar 8)
       - The CLI `python -m app.tools.approve <id>` (planned)
       - Direct call: `approval.approve(id, by='user')`
  2. `auto_approve_after_seconds` elapses (set in policy.yaml).

Approval grants notify any awaiting `await approval.wait_for(id)` call,
which then returns the decision so the original tool execution can proceed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.policy import get_policy

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    persona TEXT,
    tool TEXT NOT NULL,
    args TEXT,                      -- JSON
    requested_by TEXT NOT NULL,     -- e.g. 'user', 'heartbeat', 'autonomy'
    reason TEXT,                    -- why approval is needed
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | expired
    decided_by TEXT,
    decided_at TEXT,
    decision_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_actions(status);
CREATE INDEX IF NOT EXISTS idx_pending_ts ON pending_actions(ts);
"""


class ApprovalService:
    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "governance.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        # asyncio.Event per pending id, so callers can await a decision.
        self._waiters: dict[int, asyncio.Event] = {}
        self._waiter_lock = threading.Lock()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ---- queue -------------------------------------------------------------

    def queue(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        requested_by: str = "user",
        reason: str = "",
        persona: str | None = None,
    ) -> int:
        """Add a pending action; return its id."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO pending_actions "
                "(ts, persona, tool, args, requested_by, reason) VALUES (?,?,?,?,?,?)",
                (
                    datetime.now().isoformat(timespec="milliseconds"),
                    persona,
                    tool,
                    json.dumps(args or {}, ensure_ascii=False, default=str),
                    requested_by,
                    reason,
                ),
            )
            conn.commit()
            pid = int(cur.lastrowid)
        logger.info("Approval queued: id=%s tool=%s by=%s", pid, tool, requested_by)
        return pid

    # ---- decide ------------------------------------------------------------

    def approve(self, action_id: int, by: str = "user", note: str = "") -> bool:
        return self._decide(action_id, "approved", by, note)

    def reject(self, action_id: int, by: str = "user", note: str = "") -> bool:
        return self._decide(action_id, "rejected", by, note)

    def _decide(self, action_id: int, status: str, by: str, note: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM pending_actions WHERE id = ?", (action_id,)
            ).fetchone()
            if row is None:
                logger.warning("Approval decide: id=%s not found", action_id)
                return False
            if row["status"] != "pending":
                logger.warning("Approval decide: id=%s already %s", action_id, row["status"])
                return False
            conn.execute(
                "UPDATE pending_actions SET status=?, decided_by=?, decided_at=?, decision_note=? "
                "WHERE id=?",
                (status, by, datetime.now().isoformat(timespec="milliseconds"), note, action_id),
            )
            conn.commit()
        # wake any awaiting coroutine
        with self._waiter_lock:
            ev = self._waiters.get(action_id)
        if ev is not None:
            try:
                # Event.set is thread-safe, but if we're called from a non-event-loop
                # thread we need to ensure the event loop's scheduling sees it. asyncio.Event.set()
                # is fine to call from sync code in CPython — it just sets the flag.
                ev.set()
            except Exception:
                pass
        logger.info("Approval %s: id=%s by=%s", status, action_id, by)
        return True

    # ---- query -------------------------------------------------------------

    def get(self, action_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_actions WHERE id = ?", (action_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_pending(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_actions WHERE status='pending' ORDER BY id ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- wait --------------------------------------------------------------

    async def wait_for(self, action_id: int, timeout: float | None = None) -> dict:
        """Await a decision on `action_id`. Honours auto_approve_after_seconds.

        Returns the row (dict). Status will be one of:
          approved | rejected | expired (if auto-approve disabled and timeout hit)
        """
        # If already decided, return immediately
        row = self.get(action_id)
        if row is None:
            raise ValueError(f"Pending action {action_id} not found")
        if row["status"] != "pending":
            return row

        # Set up an asyncio.Event and register it
        ev = asyncio.Event()
        with self._waiter_lock:
            self._waiters[action_id] = ev

        try:
            policy = get_policy()
            auto_after = policy.auto_approve_after_seconds
            wait_timeout = timeout
            if auto_after > 0 and (wait_timeout is None or auto_after < wait_timeout):
                wait_timeout = float(auto_after)

            try:
                if wait_timeout:
                    await asyncio.wait_for(ev.wait(), timeout=wait_timeout)
                else:
                    await ev.wait()
            except asyncio.TimeoutError:
                # Auto-approve path
                if auto_after > 0:
                    self.approve(action_id, by="auto", note=f"auto-approved after {auto_after}s")
        finally:
            with self._waiter_lock:
                self._waiters.pop(action_id, None)

        return self.get(action_id) or {}


_singleton: ApprovalService | None = None


def get_approval() -> ApprovalService:
    global _singleton
    if _singleton is None:
        _singleton = ApprovalService()
    return _singleton
