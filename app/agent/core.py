from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, AsyncIterator

from app.agent.prompts import SYSTEM_PROMPT
from app.services.ollama import OllamaClient
from app.storage.database import Database
from app.storage.models import Message, Role

if TYPE_CHECKING:
    from app.auth.models import User
    from app.auth.rbac import RBACService
    from app.services.nexgai_client import NexgAIClient

logger = logging.getLogger(__name__)


class AgentCore:
    """Agent with 2-way routing: NexgAI agents for specialized tasks, Ollama LLM for general questions."""

    def __init__(
        self,
        ollama: OllamaClient,
        database: Database,
        nexgai_client: NexgAIClient | None = None,
    ):
        self.ollama = ollama
        self.db = database
        self.nexgai: NexgAIClient | None = nexgai_client
        self.rbac_service: RBACService | None = None
        self._prompt_override: str | None = None  # Set by learning loop when admin approves a refinement

    def _build_system_prompt(self) -> str:
        if self._prompt_override:
            return self._prompt_override
        return SYSTEM_PROMPT

    async def process_message(
        self,
        user_id: str,
        user_text: str,
        user_name: str = "User",
        user: User | None = None,
    ) -> dict:
        """Process a message and return a dict with 'text', 'message_id', 'conversation_id', 'source', 'agent_name'."""
        t0 = time.monotonic()
        conv = await self.db.get_or_create_conversation(user_id)

        user_msg = Message(role=Role.USER, content=user_text)
        await self.db.add_message(conv.id, user_msg)
        conv.messages.append(user_msg)

        event_type = "message"
        skill_name = None
        source = "local"
        success = True
        error_message = None

        try:
            # 1. Try NexgAI platform agents
            nexgai_result = await self._try_nexgai(user_id, user_name, user_text)
            if nexgai_result:
                event_type = "nexgai_execution"
                response = nexgai_result
                source = "nexgai"
                # Extract agent name from response header
                _m = re.match(r"_Handled by \*(\w+)\*", response)
                if _m:
                    skill_name = _m.group(1)
            else:
                # 2. Fall back to Ollama LLM for general questions
                event_type = "llm_conversation"
                response = await self._chat(conv)
        except Exception as e:
            success = False
            error_message = str(e)[:500]
            response = f"Error processing your request: {str(e)[:300]}"
            logger.error(f"process_message error: {e}", exc_info=True)

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        assistant_msg = Message(role=Role.ASSISTANT, content=response)
        msg_id = await self.db.add_message(conv.id, assistant_msg)

        # Log usage event for analytics
        try:
            await self.db.log_usage_event(
                event_type=event_type,
                user_id=user_id,
                skill_name=skill_name,
                response_time_ms=elapsed_ms,
                success=success,
                error_message=error_message,
            )
        except Exception as e:
            logger.warning(f"Failed to log usage event: {e}")

        return {
            "text": response,
            "message_id": msg_id,
            "conversation_id": conv.id,
            "source": source,
            "agent_name": skill_name,
        }

    async def _try_nexgai(
        self,
        user_id: str,
        user_name: str,
        text: str,
    ) -> str | None:
        """Route the request through NexgAI platform agents.

        Returns formatted response or None (falls through to Ollama LLM).
        """
        if not self.nexgai or not self.nexgai.is_available:
            return None

        try:
            # Get or create a NexgAI session for this user
            session_id = await self.db.get_nexgai_session(user_id)
            if not session_id:
                session_id = await self.nexgai.create_session()
                if not session_id:
                    return None
                await self.db.set_nexgai_session(user_id, session_id)

            # Send message to NexgAI
            result = await self.nexgai.send_message(
                message=text,
                session_id=session_id,
                user_id=user_id,
                user_name=user_name,
            )
            if not result or not result.get("success"):
                return None

            response_text = result.get("message", "")
            if not response_text.strip():
                return None

            # Format the response with the handler info
            handled_by = result.get("handled_by", "NexgAI")
            parts = [f"_Handled by *{handled_by}* (NexgAI)_\n"]
            parts.append(response_text)
            return "\n".join(parts)

        except Exception as exc:
            logger.warning("NexgAI routing failed: %s", exc)
            return None

    async def process_message_streaming(
        self,
        user_id: str,
        user_text: str,
        user_name: str = "User",
        user: User | None = None,
    ) -> AsyncIterator[dict]:
        """Process a message with streaming support for NexgAI responses.

        Yields WebSocket-ready dicts:
          {"type": "stream_start", "agent": str, "source": "nexgai"}
          {"type": "stream_chunk", "text": str}
          {"type": "stream_end", "text": str}   (full assembled response)
          {"type": "response", "text": str}      (non-streaming Ollama fallback)
        """
        # If NexgAI unavailable, go straight to Ollama LLM
        if not self.nexgai or not self.nexgai.is_available:
            result = await self.process_message(user_id, user_text, user_name, user=user)
            yield {"type": "response", **result}
            return

        # Stream through NexgAI
        t0 = time.monotonic()
        conv = await self.db.get_or_create_conversation(user_id)
        user_msg = Message(role=Role.USER, content=user_text)
        await self.db.add_message(conv.id, user_msg)

        try:
            session_id = await self.db.get_nexgai_session(user_id)
            if not session_id:
                session_id = await self.nexgai.create_session()
                if not session_id:
                    # Fall back to Ollama LLM
                    response = await self._chat(conv)
                    msg_id = await self.db.add_message(conv.id, Message(role=Role.ASSISTANT, content=response))
                    yield {"type": "response", "text": response, "message_id": msg_id,
                           "conversation_id": conv.id, "source": "local", "agent_name": None}
                    return
                await self.db.set_nexgai_session(user_id, session_id)

            handled_by = "NexgAI"
            chunks_collected: list[str] = []
            stream_started = False

            async for event in self.nexgai.stream_message(
                message=user_text,
                session_id=session_id,
                user_id=user_id,
            ):
                event_type = event.get("event", "")

                if event_type == "error":
                    # Stream failed — fall back to Ollama LLM
                    response = await self._chat(conv)
                    msg_id = await self.db.add_message(conv.id, Message(role=Role.ASSISTANT, content=response))
                    yield {"type": "response", "text": response, "message_id": msg_id,
                           "conversation_id": conv.id, "source": "local", "agent_name": None}
                    return

                if event_type in ("session", "status"):
                    if not stream_started:
                        yield {"type": "stream_start", "agent": handled_by, "source": "nexgai"}
                        stream_started = True
                    continue

                if event_type == "chunk":
                    content = event.get("content", "")
                    if content:
                        chunks_collected.append(content)
                        yield {"type": "stream_chunk", "text": content}

                if event_type == "complete":
                    handled_by = event.get("handled_by", handled_by)

            # Assemble full response
            full_text = "".join(chunks_collected)
            if not full_text.strip():
                # NexgAI returned empty stream — fall back to Ollama LLM
                response = await self._chat(conv)
                msg_id = await self.db.add_message(conv.id, Message(role=Role.ASSISTANT, content=response))
                yield {"type": "response", "text": response, "message_id": msg_id,
                       "conversation_id": conv.id, "source": "local", "agent_name": None}
                return

            formatted = f"_Handled by *{handled_by}* (NexgAI)_\n\n{full_text}"
            msg_id = await self.db.add_message(conv.id, Message(role=Role.ASSISTANT, content=formatted))

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            try:
                await self.db.log_usage_event(
                    event_type="nexgai_stream",
                    user_id=user_id,
                    response_time_ms=elapsed_ms,
                    success=True,
                )
            except Exception:
                pass

            yield {"type": "stream_end", "text": formatted, "message_id": msg_id,
                   "conversation_id": conv.id, "agent": handled_by, "source": "nexgai"}

        except Exception as exc:
            logger.error("Streaming NexgAI failed: %s", exc, exc_info=True)
            # Fall back to Ollama LLM
            response = await self._chat(conv)
            msg_id = await self.db.add_message(conv.id, Message(role=Role.ASSISTANT, content=response))
            yield {"type": "response", "text": response, "message_id": msg_id,
                   "conversation_id": conv.id, "source": "local", "agent_name": None}

    async def _chat(self, conv) -> str:
        """General-purpose LLM conversation via Ollama."""
        system = self._build_system_prompt()

        msgs = [{"role": "system", "content": system}]
        for msg in conv.messages[-20:]:
            msgs.append({"role": msg.role.value, "content": msg.content})

        try:
            result = await self.ollama.chat(messages=msgs)
            return result.get("message", {}).get("content", "").strip()
        except Exception as e:
            logger.error(f"Ollama failed: {e}", exc_info=True)
            return f"Couldn't reach Ollama. Make sure it's running and `{self.ollama.model}` is pulled."
