from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, AsyncIterator

from app.agent.prompts import SYSTEM_PROMPT, build_tool_prompt, TOOL_DEFINITIONS, TOOL_RESULT_TEMPLATE
from app.services.ollama import OllamaClient
from app.storage.database import Database
from app.storage.models import Message, Role

if TYPE_CHECKING:
    from app.agent.tools import ToolRegistry
    from app.auth.models import User
    from app.auth.rbac import RBACService
    from app.services.nexgai_client import NexgAIClient
    from app.services.agenthub_router import AgentHubRouter

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10


class AgentCore:
    """Agent with 2-way routing: NexgAI agents for specialized tasks, Ollama LLM with tool-calling for general questions."""

    def __init__(
        self,
        ollama: OllamaClient,
        database: Database,
        nexgai_client: NexgAIClient | None = None,
        tools: ToolRegistry | None = None,
        agenthub_router: AgentHubRouter | None = None,
    ):
        self.ollama = ollama
        self.db = database
        self.nexgai: NexgAIClient | None = nexgai_client
        self.tools: ToolRegistry | None = tools
        self.agenthub_router: AgentHubRouter | None = agenthub_router
        self.rbac_service: RBACService | None = None
        self._prompt_override: str | None = None  # Set by learning loop when admin approves a refinement

    def _build_system_prompt(self) -> str:
        base = self._prompt_override if self._prompt_override else SYSTEM_PROMPT
        if self.tools:
            base += "\n" + build_tool_prompt()
        return base

    async def process_message(
        self,
        user_id: str,
        user_text: str,
        user_name: str = "User",
        user: User | None = None,
        conversation_id: str | None = None,
    ) -> dict:
        """Process a message and return a dict with 'text', 'message_id', 'conversation_id', 'source', 'agent_name'."""
        # Set user context for tools that need it (reminders, etc.)
        if self.tools:
            self.tools._reminder_user_id = user_id
        t0 = time.monotonic()
        if conversation_id:
            conv = await self.db.get_conversation_by_id(conversation_id)
            if not conv:
                conv = await self.db.get_or_create_conversation(user_id)
        else:
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
            # 0. Try AgentHub first (if enabled) — the newer, governed gateway
            ah_handled = False
            if self.agenthub_router:
                try:
                    ah_result = await self.agenthub_router.route(
                        message=user_text,
                        user_id=user_id,
                        user=user,
                        conversation_id=conv.id,
                    )
                    if ah_result:
                        response = ah_result.get("text", "")
                        source = "agenthub"
                        skill_name = ah_result.get("agent_name")
                        event_type = "agenthub_execution"
                        ah_handled = True
                except Exception as ah_exc:
                    logger.warning("AgentHub routing failed: %s — falling through", ah_exc)

            if not ah_handled:
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
                    # 2. Fall back to Ollama LLM with tool-calling
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

            # Skip generic stub responses — let Ollama handle properly
            stub_phrases = [
                "i'm here to help",
                "how can i assist you",
                "how may i help you",
            ]
            if any(phrase in response_text.lower() for phrase in stub_phrases):
                logger.info("NexgAI returned stub response, falling through to Ollama")
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
        conversation_id: str | None = None,
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
            result = await self.process_message(user_id, user_text, user_name, user=user, conversation_id=conversation_id)
            yield {"type": "response", **result}
            return

        # Stream through NexgAI
        t0 = time.monotonic()
        if conversation_id:
            conv = await self.db.get_conversation_by_id(conversation_id)
            if not conv:
                conv = await self.db.get_or_create_conversation(user_id)
        else:
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
        """Hybrid agent: LLM classifies intent → code executes tools → LLM synthesizes response."""
        system = self._build_system_prompt()

        msgs = [{"role": "system", "content": system}]
        for msg in conv.messages[-20:]:
            msgs.append({"role": msg.role.value, "content": msg.content})

        if not self.tools:
            try:
                result = await self.ollama.chat(messages=msgs)
                return result.get("message", {}).get("content", "").strip()
            except Exception as e:
                logger.error(f"Ollama failed: {e}", exc_info=True)
                return f"Couldn't reach Ollama. Make sure it's running and `{self.ollama.model}` is pulled."

        # Step 1: Ask LLM to classify — should it use a tool or answer directly?
        content = ""
        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                result = await self.ollama.chat(messages=msgs)
            except Exception as e:
                logger.error(f"Ollama failed: {e}", exc_info=True)
                return f"Couldn't reach Ollama. Make sure it's running."

            message = result.get("message", {})
            content = message.get("content", "").strip()
            logger.info(f"LLM response (round {round_num}): {content[:200]}")

            # Check if LLM output a tool call block
            from app.agent.tools import ToolRegistry as TR
            parsed = TR.parse_tool_call(content)

            if not parsed:
                # No tool call — check if LLM is faking an action
                # If the response looks like it's describing a tool action, force a classification
                if round_num == 0 and self._looks_like_fake_action(content):
                    logger.info("LLM faked a tool action, forcing re-classification")
                    msgs.append({"role": "assistant", "content": content})
                    msgs.append({"role": "user", "content": (
                        "You described the action but did NOT execute it. "
                        "You MUST output a ```tool block to actually perform it. "
                        "Output ONLY the tool block now."
                    )})
                    continue
                # Genuine answer — return it
                return content

            # Tool call found — execute it
            tool_name = parsed.get("name", "")
            arguments = parsed.get("arguments", {})
            logger.info(f"Tool call: {tool_name}({arguments})")
            tool_result = await self.tools.execute(tool_name, arguments)

            # Step 2: Send tool result back to LLM for natural response
            msgs.append({"role": "assistant", "content": content})
            msgs.append({
                "role": "user",
                "content": TOOL_RESULT_TEMPLATE.format(
                    tool_name=tool_name, result=tool_result
                ),
            })
            # Continue loop — LLM will either answer or call another tool

        return content or "Sorry, I couldn't complete that request. Please try rephrasing."

    @staticmethod
    def _looks_like_fake_action(text: str) -> bool:
        """Detect if the LLM described an action instead of executing it."""
        lower = text.lower()
        fake_phrases = [
            "email drafted", "email sent", "i have sent",
            "i have drafted", "reminder set", "reminder:", "*reminder:*",
            "whatsapp message sent", "i have set a reminder",
            "the email has been", "your reminder has been",
            "message has been sent", "i've drafted", "i've sent",
            "i have deleted", "i have removed", "i've deleted", "i've removed",
            "files have been deleted", "files have been removed",
            "i have erased", "successfully deleted", "successfully removed",
            "have been wiped", "desktop has been cleared", "all files removed",
        ]
        # Only flag as fake if there's NO tool block in the text
        if "```tool" in lower or '{"name"' in lower:
            return False
        return any(phrase in lower for phrase in fake_phrases)
