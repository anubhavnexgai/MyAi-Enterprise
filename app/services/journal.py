"""EpisodicJournal — append-only JSONL log of every conversation turn, per persona.

Layout (under app/workspace/):
    journal/YYYY-MM-DD.jsonl                  — default persona
    agents/<name>/journal/YYYY-MM-DD.jsonl    — per-persona

Each line is a JSON object:
    {
      "ts": "2026-04-27T13:05:00.123",
      "user_msg": "...",
      "response": "...",
      "tool_calls": [{"name": "...", "args": {...}, "result": "..."}],
      "source": "local|nexgai|agenthub",
      "elapsed_ms": 1234,
      "persona": "default"
    }

The journal is the raw substrate the dreaming/diary loop consumes to produce
summaries and extract long-term facts. Keep entries small — truncate large
tool results before logging.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PERSONA = "default"
MAX_TOOL_RESULT_CHARS = 2000  # truncate per-tool result to keep journal sane


class EpisodicJournal:
    """Per-persona, per-day JSONL journal."""

    def __init__(self, workspace_root: Path | str | None = None):
        if workspace_root is None:
            workspace_root = Path(__file__).parent.parent / "workspace"
        self.root = Path(workspace_root)
        self._lock = threading.Lock()  # journal append must be atomic across threads

    # ---- paths -------------------------------------------------------------

    def _journal_dir(self, persona: str) -> Path:
        if persona == DEFAULT_PERSONA:
            return self.root / "journal"
        return self.root / "agents" / persona / "journal"

    def path_for(self, persona: str, on: date | None = None) -> Path:
        on = on or date.today()
        return self._journal_dir(persona) / f"{on.isoformat()}.jsonl"

    # ---- write -------------------------------------------------------------

    def append(
        self,
        persona: str,
        user_msg: str,
        response: str,
        tool_calls: list[dict] | None = None,
        source: str = "local",
        elapsed_ms: int | None = None,
    ) -> None:
        """Append one turn to today's journal for `persona`."""
        entry = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "persona": persona,
            "user_msg": user_msg,
            "response": response,
            "tool_calls": [self._truncate_tool_call(tc) for tc in (tool_calls or [])],
            "source": source,
            "elapsed_ms": elapsed_ms,
        }
        path = self.path_for(persona)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with self._lock:
                with path.open("a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as exc:
            # Journal failures must never break the chat path.
            logger.warning("Journal append failed (persona=%s): %s", persona, exc)

    @staticmethod
    def _truncate_tool_call(tc: dict) -> dict:
        result = tc.get("result", "")
        if isinstance(result, str) and len(result) > MAX_TOOL_RESULT_CHARS:
            result = result[:MAX_TOOL_RESULT_CHARS] + f"…[truncated {len(result) - MAX_TOOL_RESULT_CHARS} chars]"
        return {
            "name": tc.get("name", ""),
            "args": tc.get("args", {}),
            "result": result,
        }

    # ---- read --------------------------------------------------------------

    def read_day(self, persona: str, on: date | None = None) -> list[dict]:
        """Return all entries for one persona-day, oldest first. Empty if none."""
        path = self.path_for(persona, on)
        if not path.is_file():
            return []
        entries: list[dict] = []
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        logger.warning("Skipping malformed journal line: %s", exc)
        except Exception as exc:
            logger.warning("Journal read failed (%s): %s", path, exc)
        return entries

    def list_dates(self, persona: str) -> list[date]:
        """Return all dates that have journal entries for this persona."""
        d = self._journal_dir(persona)
        if not d.is_dir():
            return []
        out: list[date] = []
        for f in d.glob("*.jsonl"):
            try:
                out.append(date.fromisoformat(f.stem))
            except ValueError:
                continue
        return sorted(out)


# ---- module-level singleton ------------------------------------------------

_singleton: EpisodicJournal | None = None


def get_journal() -> EpisodicJournal:
    global _singleton
    if _singleton is None:
        _singleton = EpisodicJournal()
    return _singleton
