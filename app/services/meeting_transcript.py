"""Meeting transcript service: ingests transcript lines, debounces,
generates suggestions via Ollama, and delivers them to the user.

Transcript text is fed in via the /transcript paste command in Slack.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, TYPE_CHECKING

from app.agent.prompts import (
    MEETING_SUGGESTION_SYSTEM_PROMPT,
    MEETING_SUGGESTION_USER_PROMPT,
)
from app.config import settings
from app.services.ollama import OllamaClient

if TYPE_CHECKING:
    from app.storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class MeetingSession:
    """Tracks state for a single active meeting."""

    call_id: str
    user_id: str
    user_name: str = "User"
    user_role: str = "Participant"
    meeting_subject: str = ""
    conversation_reference: dict = field(default_factory=dict)
    transcript_lines: list[str] = field(default_factory=list)
    last_suggestion: str = ""
    last_suggestion_hash: str = ""
    last_suggestion_time: float = 0.0
    _pending_task: asyncio.Task | None = field(default=None, repr=False)


def _content_hash(text: str) -> str:
    """Return a short hash of the meaningful content for dedup."""
    normalized = " ".join(text.split())
    return hashlib.md5(normalized.encode()).hexdigest()


class MeetingTranscriptService:
    """Manages active meeting sessions and suggestion generation."""

    def __init__(
        self,
        ollama: OllamaClient,
        deliver_fn: Callable[[MeetingSession, str], Awaitable[None]] | None = None,
        database: Database | None = None,
    ):
        self.ollama = ollama
        self.deliver_fn = deliver_fn
        self.database = database
        self._sessions: dict[str, MeetingSession] = {}  # keyed by call_id
        self._debounce_seconds = settings.meeting_suggestion_debounce_seconds
        self._max_transcript_chars = settings.meeting_transcript_max_chars

    # -- Session lifecycle --

    def start_session(
        self,
        call_id: str,
        user_id: str,
        user_name: str = "User",
        user_role: str = "Participant",
        meeting_subject: str = "",
        conversation_reference: dict | None = None,
    ) -> MeetingSession:
        session = MeetingSession(
            call_id=call_id,
            user_id=user_id,
            user_name=user_name,
            user_role=user_role,
            meeting_subject=meeting_subject,
            conversation_reference=conversation_reference or {},
        )
        self._sessions[call_id] = session
        logger.info(f"Meeting session started: call_id={call_id}, user={user_name}")
        return session

    def get_session(self, call_id: str) -> MeetingSession | None:
        return self._sessions.get(call_id)

    def end_session(self, call_id: str) -> None:
        session = self._sessions.pop(call_id, None)
        if session and session._pending_task and not session._pending_task.done():
            session._pending_task.cancel()
        if session:
            logger.info(f"Meeting session ended: call_id={call_id}")
            if self.database and session.transcript_lines:
                asyncio.create_task(self._save_meeting_summary(session))

    async def _save_meeting_summary(self, session: MeetingSession) -> None:
        """Generate and save a meeting summary when a session ends."""
        try:
            transcript = self.get_rolling_transcript(session)
            if not transcript.strip():
                return

            messages = [
                {"role": "system", "content": "Summarize this meeting transcript concisely. "
                 "Output two sections: SUMMARY (2-3 sentences) and KEY_POINTS (bullet list of decisions/action items)."},
                {"role": "user", "content": transcript},
            ]
            result = await self.ollama.chat(messages=messages)
            summary_text = result.get("message", {}).get("content", "").strip()

            summary = summary_text
            key_points = ""
            if "KEY_POINTS" in summary_text:
                parts = summary_text.split("KEY_POINTS", 1)
                summary = parts[0].replace("SUMMARY", "").strip().strip(":")
                key_points = parts[1].strip().strip(":")

            await self.database.save_meeting_summary(
                user_id=session.user_id,
                call_id=session.call_id,
                meeting_subject=session.meeting_subject,
                summary=summary,
                key_points=key_points,
            )
            logger.info(f"Meeting summary saved for call_id={session.call_id}")
        except Exception as e:
            logger.error(f"Failed to save meeting summary: {e}", exc_info=True)

    def get_session_by_user(self, user_id: str) -> MeetingSession | None:
        for session in self._sessions.values():
            if session.user_id == user_id:
                return session
        return None

    @property
    def active_sessions(self) -> dict[str, MeetingSession]:
        return dict(self._sessions)

    # -- Transcript ingestion --

    def get_rolling_transcript(self, session: MeetingSession) -> str:
        """Return the full transcript, trimmed to max chars from the end."""
        full = "\n".join(session.transcript_lines)
        if len(full) > self._max_transcript_chars:
            full = full[-self._max_transcript_chars:]
            newline_idx = full.find("\n")
            if newline_idx != -1:
                full = full[newline_idx + 1:]
        return full

    async def ingest_transcript(self, call_id: str, transcript_text: str) -> None:
        """Ingest new transcript text for a meeting session.

        Debounces suggestion generation: schedules it after debounce_seconds,
        resetting the timer if new text arrives before it fires.
        """
        session = self._sessions.get(call_id)
        if not session:
            logger.warning(f"No active session for call_id={call_id}, ignoring transcript")
            return

        new_lines = self._parse_transcript_text(transcript_text)
        if not new_lines:
            return

        session.transcript_lines.extend(new_lines)
        logger.info(f"Ingested {len(new_lines)} transcript lines for call_id={call_id}")

        # Cancel any pending debounce task and schedule a new one
        if session._pending_task and not session._pending_task.done():
            session._pending_task.cancel()

        session._pending_task = asyncio.create_task(
            self._debounced_suggest(session)
        )

    @staticmethod
    def _parse_transcript_text(raw: str) -> list[str]:
        """Parse VTT or plain transcript text into meaningful lines."""
        lines = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if line.upper() == "WEBVTT":
                continue
            if line.startswith("NOTE") or line.startswith("STYLE"):
                continue
            if "-->" in line:
                continue
            if line.isdigit():
                continue
            lines.append(line)
        return lines

    # -- Suggestion generation --

    async def _debounced_suggest(self, session: MeetingSession) -> None:
        """Wait for debounce period, then generate and deliver suggestion."""
        try:
            await asyncio.sleep(self._debounce_seconds)
        except asyncio.CancelledError:
            return

        await self.generate_and_deliver(session)

    async def generate_and_deliver(self, session: MeetingSession) -> str | None:
        """Generate a suggestion and deliver it. Returns the suggestion or None."""
        transcript = self.get_rolling_transcript(session)
        if not transcript.strip():
            logger.info("Empty transcript, skipping suggestion")
            return None

        current_hash = _content_hash(transcript)
        if current_hash == session.last_suggestion_hash:
            logger.info("Transcript unchanged since last suggestion, skipping")
            return None

        now = time.time()
        elapsed = now - session.last_suggestion_time
        if elapsed < self._debounce_seconds and session.last_suggestion_hash:
            logger.info(f"Only {elapsed:.1f}s since last suggestion, skipping")
            return None

        suggestion = await self._call_ollama(session, transcript)
        if not suggestion or suggestion.strip() == "NO_SUGGESTION":
            logger.info("Model returned NO_SUGGESTION")
            return None

        suggestion_hash = _content_hash(suggestion)
        if suggestion_hash == _content_hash(session.last_suggestion):
            logger.info("Duplicate suggestion, skipping delivery")
            return None

        session.last_suggestion = suggestion
        session.last_suggestion_hash = current_hash
        session.last_suggestion_time = now

        if self.deliver_fn:
            try:
                await self.deliver_fn(session, suggestion)
            except Exception as e:
                logger.error(f"Failed to deliver suggestion: {e}", exc_info=True)

        return suggestion

    async def _build_meeting_context(self, session: MeetingSession) -> str:
        """Build rich meeting context from user profile + meeting history + subject."""
        parts = []
        if session.meeting_subject:
            parts.append(f"Current meeting: {session.meeting_subject}")

        if self.database:
            profile = await self.database.get_user_profile(session.user_id)
            if profile:
                if profile.get("role"):
                    session.user_role = profile["role"]
                if profile.get("name"):
                    session.user_name = profile["name"]
                if profile.get("bio"):
                    parts.append(f"About the user: {profile['bio']}")

            contexts = await self.database.get_all_contexts(session.user_id)
            if contexts:
                parts.append("\n## User's Knowledge Base")
                for ctx in contexts:
                    parts.append(f"\n### {ctx['name']}")
                    parts.append(ctx['content'])

            recent = await self.database.get_recent_meetings(session.user_id, limit=3)
            if recent:
                parts.append("\n## Recent meetings this user attended")
                for m in recent:
                    line = f"- {m['meeting_subject'] or 'Untitled'}"
                    if m.get("key_points"):
                        line += f": {m['key_points']}"
                    parts.append(line)

        if not parts:
            parts.append("General meeting")

        return "\n".join(parts)

    async def _call_ollama(self, session: MeetingSession, transcript: str) -> str:
        """Build the prompt and call Ollama for a suggestion."""
        meeting_context = await self._build_meeting_context(session)

        system_prompt = MEETING_SUGGESTION_SYSTEM_PROMPT.format(
            user_name=session.user_name,
            user_role=session.user_role,
            meeting_context=meeting_context,
        )

        user_prompt = MEETING_SUGGESTION_USER_PROMPT.format(
            transcript=transcript,
            user_name=session.user_name,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        original_model = self.ollama.model
        suggestion_model = settings.meeting_suggestion_model
        if suggestion_model:
            self.ollama.set_model(suggestion_model)

        try:
            result = await self.ollama.chat(messages=messages)
            return result.get("message", {}).get("content", "").strip()
        except Exception as e:
            logger.error(f"Ollama suggestion generation failed: {e}", exc_info=True)
            return ""
        finally:
            if suggestion_model:
                self.ollama.set_model(original_model)
