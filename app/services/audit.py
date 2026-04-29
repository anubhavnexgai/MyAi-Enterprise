"""AuditService — append-only action log in SQLite.

Every tool call (and every approval decision) gets a row. The log is meant
to answer: "what did MyAi do, when, on whose behalf, what did it return,
and why was it allowed?"

Schema is tiny on purpose. Storage backend is `data/governance.db`,
separate from the main MyAi DB so it can be backed up / inspected in
isolation.
"""
from __future__ import annotations

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
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,            -- who initiated (e.g. 'user', 'persona:sam', 'heartbeat')
    persona TEXT,                   -- which persona was active
    action TEXT NOT NULL,           -- tool name or governance event
    inputs TEXT,                    -- JSON-encoded inputs (truncated)
    outputs TEXT,                   -- JSON-encoded outputs (truncated)
    decision TEXT NOT NULL,         -- 'allowed' | 'queued' | 'blocked' | 'approved' | 'rejected'
    reason TEXT                     -- why (e.g. 'unlisted host', 'approval-required')
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_decision ON audit_log(decision);
"""


class AuditService:
    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "governance.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        # SQLite connections are per-thread; opening fresh per write keeps
        # the code simple and avoids cross-thread misuse.
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ---- record ------------------------------------------------------------

    def record(
        self,
        actor: str,
        action: str,
        decision: str,
        inputs: Any = None,
        outputs: Any = None,
        reason: str = "",
        persona: str | None = None,
    ) -> int:
        """Write one audit row. Best-effort — errors are logged, not raised."""
        policy = get_policy()
        if not policy.audit_enabled:
            return 0

        max_chars = policy.audit_max_chars
        level = policy.audit_level

        if level == "summary":
            inputs_json, outputs_json = "", ""
        else:
            inputs_json = self._truncate_json(inputs, max_chars)
            outputs_json = self._truncate_json(outputs, max_chars)

        try:
            with self._lock, self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO audit_log (ts, actor, persona, action, inputs, outputs, decision, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now().isoformat(timespec="milliseconds"),
                        actor,
                        persona,
                        action,
                        inputs_json,
                        outputs_json,
                        decision,
                        reason,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
        except Exception as exc:
            logger.warning("Audit insert failed: %s", exc)
            return 0

    @staticmethod
    def _truncate_json(value: Any, max_chars: int) -> str:
        if value is None:
            return ""
        try:
            s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            s = str(value)
        if len(s) > max_chars:
            s = s[:max_chars] + f"…[truncated {len(s) - max_chars} chars]"
        return s

    # ---- read --------------------------------------------------------------

    def tail(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])


_singleton: AuditService | None = None


def get_audit() -> AuditService:
    global _singleton
    if _singleton is None:
        _singleton = AuditService()
    return _singleton
