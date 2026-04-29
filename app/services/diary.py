"""DiaryService — the "dreaming" loop.

Once a day (or on demand), reads a persona's journal for that day, asks Ollama
to write a short diary entry plus extract durable facts about the user, then:

  1. Writes `workspace/agents/<persona>/diary/YYYY-MM-DD.md`
     (or `workspace/diary/YYYY-MM-DD.md` for the default persona).
  2. Appends extracted facts to `workspace/user.md` under the
     `<!-- DREAMING_APPEND_BELOW -->` marker, deduped against existing facts.
  3. Invalidates the PersonaLoader cache so the next chat turn sees the
     updated user.md.

This is what makes MyAi *learn* over time. The journal records the raw turns;
the diary distills what mattered; user.md is where lessons stick.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from app.agent.persona import DEFAULT_PERSONA, get_persona_loader
from app.services.journal import get_journal
from app.services.ollama import OllamaClient

logger = logging.getLogger(__name__)

DREAMING_MARKER = "<!-- DREAMING_APPEND_BELOW -->"

# Cap how much of the journal we feed the model — qwen2.5:7b context is finite.
MAX_JOURNAL_CHARS = 12000

DIARY_SYSTEM_PROMPT = """You are the "dreaming" loop of an AI assistant. Once a
day you read the assistant's journal of conversations and produce two things:

1. A short diary entry (3-6 sentences) written in first person from the
   assistant's perspective. Cover: what we worked on, what went well, what
   went wrong, what the user seemed to care about. Be honest — mention
   mistakes and corrections. Don't pad. Don't add bullet headers.

2. A list of NEW durable facts about the user, in the format:
   - <fact in one short line>
   Only include facts that:
     - Are about the user themselves (preferences, habits, people, projects,
       constraints, tools they use, opinions they expressed).
     - Are likely to still be true in a week.
     - Are not already obvious from the user's profile.
   Skip ephemeral things like "user asked about X today."

Output EXACTLY this format and nothing else:

DIARY:
<diary text>

FACTS:
- <fact 1>
- <fact 2>
(or "FACTS:\\n(none)" if no new durable facts)
"""


class DiaryService:
    """Reads a journal day, writes a diary, distills facts into user.md."""

    def __init__(
        self,
        ollama: OllamaClient | None = None,
        workspace_root: Path | str | None = None,
    ):
        self.ollama = ollama or OllamaClient()
        if workspace_root is None:
            workspace_root = Path(__file__).parent.parent / "workspace"
        self.root = Path(workspace_root)
        self.journal = get_journal()
        self.loader = get_persona_loader()

    # ---- public API --------------------------------------------------------

    async def consolidate(
        self,
        persona: str = DEFAULT_PERSONA,
        on: date | None = None,
    ) -> dict:
        """Run the dreaming job for one persona-day.

        Returns: {"status": "...", "diary_path": str|None, "facts_added": int,
                  "entries_processed": int, "diary_text": str, "facts": [..]}
        """
        on = on or date.today()
        entries = self.journal.read_day(persona, on)
        if not entries:
            return {
                "status": "no_journal",
                "diary_path": None,
                "facts_added": 0,
                "entries_processed": 0,
                "diary_text": "",
                "facts": [],
            }

        journal_text = self._format_journal_for_prompt(entries)
        diary_text, facts = await self._llm_consolidate(persona, on, journal_text)

        diary_path = self._write_diary(persona, on, diary_text, facts, len(entries))
        facts_added = self._append_facts_to_user_md(facts)

        if facts_added > 0:
            # Persona prompts cache user.md — invalidate so the next chat
            # turn picks up the new facts.
            self.loader.invalidate()

        return {
            "status": "ok",
            "diary_path": str(diary_path),
            "facts_added": facts_added,
            "entries_processed": len(entries),
            "diary_text": diary_text,
            "facts": facts,
        }

    # ---- internals ---------------------------------------------------------

    def _format_journal_for_prompt(self, entries: list[dict]) -> str:
        """Turn JSONL entries into a compact text block for the LLM."""
        lines: list[str] = []
        running_chars = 0
        for e in entries:
            ts = e.get("ts", "")
            user = (e.get("user_msg") or "").strip()
            resp = (e.get("response") or "").strip()
            tools = e.get("tool_calls") or []
            block = [f"[{ts}]"]
            if user:
                block.append(f"USER: {user}")
            for tc in tools:
                name = tc.get("name", "?")
                args = tc.get("args", {})
                block.append(f"TOOL: {name}({args})")
            if resp:
                block.append(f"ASSISTANT: {resp}")
            chunk = "\n".join(block)
            if running_chars + len(chunk) > MAX_JOURNAL_CHARS:
                lines.append("…[journal truncated for length]")
                break
            lines.append(chunk)
            running_chars += len(chunk)
        return "\n\n".join(lines)

    async def _llm_consolidate(
        self,
        persona: str,
        on: date,
        journal_text: str,
    ) -> tuple[str, list[str]]:
        """Call Ollama; return (diary_text, facts_list)."""
        user_prompt = (
            f"Persona: {persona}\n"
            f"Date: {on.isoformat()}\n\n"
            f"Today's journal:\n\n{journal_text}\n\n"
            "Now produce the DIARY and FACTS sections."
        )
        try:
            result = await self.ollama.chat(messages=[
                {"role": "system", "content": DIARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ])
            content = result.get("message", {}).get("content", "").strip()
        except Exception as exc:
            logger.warning("Dreaming LLM call failed: %s", exc)
            return (f"(dreaming failed: {exc})", [])

        return self._parse_dreaming_output(content)

    @staticmethod
    def _parse_dreaming_output(content: str) -> tuple[str, list[str]]:
        """Pull DIARY and FACTS blocks out of the LLM's output."""
        diary = ""
        facts: list[str] = []

        # Match DIARY: ... up until FACTS: or end
        m = re.search(r"DIARY:\s*(.*?)(?:\n\s*FACTS:|$)", content, re.DOTALL | re.IGNORECASE)
        if m:
            diary = m.group(1).strip()
        else:
            # Model didn't follow format — fall back to the whole response as diary
            diary = content

        m2 = re.search(r"FACTS:\s*(.*)$", content, re.DOTALL | re.IGNORECASE)
        if m2:
            facts_block = m2.group(1).strip()
            if facts_block.lower() not in ("(none)", "none", ""):
                for line in facts_block.splitlines():
                    line = line.strip()
                    if line.startswith(("-", "*", "•")):
                        line = line[1:].strip()
                    if line and line.lower() not in ("(none)", "none"):
                        facts.append(line)
        return diary, facts

    def _write_diary(
        self,
        persona: str,
        on: date,
        diary_text: str,
        facts: list[str],
        entry_count: int,
    ) -> Path:
        if persona == DEFAULT_PERSONA:
            d = self.root / "diary"
        else:
            d = self.root / "agents" / persona / "diary"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{on.isoformat()}.md"
        body_lines = [
            f"# Diary — {on.isoformat()} ({persona})",
            "",
            f"_{entry_count} journal entries consolidated._",
            "",
            diary_text.strip(),
            "",
        ]
        if facts:
            body_lines.append("## Facts extracted")
            for f in facts:
                body_lines.append(f"- {f}")
            body_lines.append("")
        path.write_text("\n".join(body_lines), encoding="utf-8")
        return path

    def _append_facts_to_user_md(self, facts: list[str]) -> int:
        """Append new facts to user.md under the dreaming marker.

        Dedupes against the file's existing content (case-insensitive substring
        match) so the same fact doesn't accumulate.

        Returns the number of facts actually written.
        """
        if not facts:
            return 0
        user_md = self.root / "user.md"
        try:
            current = user_md.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("user.md missing — creating one with marker")
            current = f"# User\n\n{DREAMING_MARKER}\n"
            user_md.write_text(current, encoding="utf-8")

        # Ensure the marker exists; append it if the user removed it.
        if DREAMING_MARKER not in current:
            current = current.rstrip() + f"\n\n{DREAMING_MARKER}\n"

        existing_lower = current.lower()
        new_facts: list[str] = []
        for f in facts:
            f_clean = f.strip().rstrip(".")
            if not f_clean:
                continue
            # Dedupe: skip if substring already present somewhere in the file.
            if f_clean.lower() in existing_lower:
                continue
            new_facts.append(f_clean)

        if not new_facts:
            return 0

        # Insert after marker (before any later content the user has put below)
        marker_idx = current.find(DREAMING_MARKER)
        insert_at = marker_idx + len(DREAMING_MARKER)
        addition = "\n" + "\n".join(f"- {f}" for f in new_facts) + "\n"
        new_content = current[:insert_at] + addition + current[insert_at:]
        user_md.write_text(new_content, encoding="utf-8")
        return len(new_facts)


# ---- module-level singleton ------------------------------------------------

_singleton: DiaryService | None = None


def get_diary_service(ollama: OllamaClient | None = None) -> DiaryService:
    global _singleton
    if _singleton is None:
        _singleton = DiaryService(ollama=ollama)
    return _singleton
