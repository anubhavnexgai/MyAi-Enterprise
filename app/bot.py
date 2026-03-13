from __future__ import annotations

import logging
import re
from pathlib import Path

from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import Activity, ActivityTypes

from app.agent.core import AgentCore
from app.config import permissions_config
from app.security.permissions import auth_service, permission_manager
from app.services.meeting_transcript import MeetingTranscriptService
from app.services.web_search import WebSearchService

logger = logging.getLogger(__name__)


class MyAiBot(ActivityHandler):
    """Microsoft Teams bot that routes messages to the MyAi agent."""

    def __init__(
        self,
        agent: AgentCore,
        search_service: WebSearchService,
        graph_client=None,
        meeting_service: MeetingTranscriptService | None = None,
    ):
        self.agent = agent
        self.search_service = search_service
        self.graph_client = graph_client
        self.meeting_service = meeting_service
        # Stores context for pending /join calls so the calling webhook
        # can associate the call_id with user info and conversation ref
        self._pending_join_context: dict[str, dict] = {}

    async def on_message_activity(self, turn_context: TurnContext):
        user_id = turn_context.activity.from_property.id
        user_name = turn_context.activity.from_property.name or "User"
        # Strip the @mention so we can parse slash commands
        text = TurnContext.remove_recipient_mention(turn_context.activity)
        text = (text or turn_context.activity.text or "").strip()

        if not text:
            return

        # Auth check
        if not auth_service.is_user_allowed(user_id):
            await turn_context.send_activity("⛔ You are not authorized to use MyAi.")
            return

        # Check for slash commands
        if text.startswith("/"):
            response = await self._handle_command(text, user_id, user_name, turn_context)
            if response:
                await turn_context.send_activity(response)
                return

        # Send typing indicator
        await turn_context.send_activities([
            Activity(type=ActivityTypes.typing)
        ])

        # Process through agent
        try:
            response = await self.agent.process_message(user_id, text)
        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            response = f"⚠️ Something went wrong: {str(e)[:200]}"

        # Teams has a 4096 char limit per message — split if needed
        if len(response) > 4000:
            chunks = [response[i:i+4000] for i in range(0, len(response), 4000)]
            for chunk in chunks:
                await turn_context.send_activity(chunk)
        else:
            await turn_context.send_activity(response)

    async def _handle_command(self, text: str, user_id: str, user_name: str, turn_context: TurnContext) -> str | None:
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if command == "/help":
            return (
                "**MyAi Commands**\n\n"
                "- `/model <name>` -- Switch Ollama model\n"
                "- `/status` -- Show current config and health\n"
                "- `/allow <path>` -- Grant file access to a directory\n"
                "- `/revoke` -- Revoke all file permissions\n"
                "- `/search on|off` -- Toggle web search\n"
                "- `/index <path>` -- Index a directory for RAG\n"
                "- `/join [url]` -- Join a meeting (auto-detected when added to meeting)\n"
                "- `/clear` -- Clear conversation history\n"
                "- `/help` -- Show this message"
            )

        elif command == "/status":
            ollama_ok = await self.agent.ollama.health_check()
            models = []
            if ollama_ok:
                try:
                    model_list = await self.agent.ollama.list_models()
                    models = [m.get("name", "?") for m in model_list[:10]]
                except Exception:
                    pass

            search_status = "🟢 On" if permission_manager.is_search_enabled(user_id) else "🔴 Off"
            dirs = permissions_config.allowed_dirs or ["None"]

            return (
                f"🐾 **MyAi Status**\n\n"
                f"**Ollama:** {'🟢 Connected' if ollama_ok else '🔴 Not reachable'}\n"
                f"**Model:** `{self.agent.ollama.model}`\n"
                f"**Available models:** {', '.join(models) or 'N/A'}\n"
                f"**Web search:** {search_status}\n"
                f"**Allowed dirs:** {chr(10).join(dirs)}\n"
                f"**User:** {user_name} (`{user_id[:16]}...`)"
            )

        elif command == "/model":
            if not arg:
                return "Usage: `/model <model_name>` (e.g., `/model mistral:7b`)"
            self.agent.ollama.set_model(arg)
            return f"✅ Switched to model: `{arg}`"

        elif command == "/allow":
            if not arg:
                return "Usage: `/allow <directory_path>` (e.g., `/allow /home/user/projects`)"
            resolved = str(Path(arg).resolve())
            if not Path(resolved).exists():
                return f"⚠️ Directory not found: `{arg}`"
            if not Path(resolved).is_dir():
                return f"⚠️ Not a directory: `{arg}`"
            permissions_config.grant_directory(resolved)
            permission_manager.grant(user_id, f"dir:{resolved}")
            return f"✅ Granted access to: `{resolved}`"

        elif command == "/revoke":
            permissions_config.revoke_all()
            permission_manager.revoke_all(user_id)
            return "✅ All file permissions revoked."

        elif command == "/search":
            if arg.lower() in ("on", "true", "enable", "1"):
                self.search_service.toggle(True)
                permission_manager.set_search_enabled(user_id, True)
                return "🔍 Web search **enabled**. The agent can now search the web when needed."
            elif arg.lower() in ("off", "false", "disable", "0"):
                self.search_service.toggle(False)
                permission_manager.set_search_enabled(user_id, False)
                return "🔍 Web search **disabled**."
            else:
                return "Usage: `/search on` or `/search off`"

        elif command == "/index":
            if not arg:
                return "Usage: `/index <directory_path>`"
            resolved = str(Path(arg).resolve())
            if not permissions_config.is_path_allowed(resolved):
                return f"⚠️ Directory not in allowlist. Run `/allow {arg}` first."
            try:
                result = await self.agent.tools.rag_service.index_directory(resolved)
                return f"⏳ Indexing complete!\n\n✅ {result}"
            except Exception as e:
                return f"❌ Indexing failed: {e}"

        elif command == "/clear":
            await self.agent.db.clear_conversation(user_id)
            return "✅ Conversation history cleared."
            
        elif command == "/join":
            if not self.graph_client:
                return "Graph Client not configured."

            # Try to find the join URL from: explicit arg > HTML-embedded > channel_data
            join_url = arg.strip()

            # Teams might wrap the URL in HTML tags
            if join_url:
                url_match = re.search(
                    r'(https://teams\.microsoft\.com/(?:l/meetup-join|meet)/[^\s>|]+)', join_url
                )
                if url_match:
                    join_url = url_match.group(1)
                else:
                    m = re.search(r'href=["\']([^"\']+)["\']', join_url)
                    if m:
                        join_url = m.group(1)
                    elif "<" in join_url and ">" in join_url:
                        join_url = re.sub(r'<[^>]+>', '', join_url).strip()

            # Fall back to meeting context from channel_data
            if not join_url or "http" not in join_url:
                join_url = self._extract_meeting_join_url(turn_context)

            if not join_url:
                return (
                    "I couldn't find the meeting link. Either:\n"
                    "1. Add me directly to the meeting (I'll join automatically), or\n"
                    "2. Run: `/join <meeting-url>`"
                )

            await self._auto_join_meeting(turn_context, join_url, user_id, user_name)
            return None  # _auto_join_meeting sends its own messages

        return None  # Not a recognized command — pass to agent

    # ── Auto-join meeting support ──

    def _extract_meeting_join_url(self, turn_context: TurnContext) -> str | None:
        """Extract a meeting join URL from the activity's channel_data."""
        chan_data = turn_context.activity.channel_data or {}
        # Teams nests meeting info in different places depending on event type
        meeting = chan_data.get("meeting", {})
        join_url = (
            meeting.get("joinUrl")
            or meeting.get("joinWebUrl")
            or chan_data.get("joinUrl")
            or chan_data.get("joinWebUrl")
        )
        return join_url if join_url and "http" in join_url else None

    async def _auto_join_meeting(
        self, turn_context: TurnContext, join_url: str, user_id: str, user_name: str
    ) -> None:
        """Join a meeting automatically and set up the transcript listener."""
        if not self.graph_client:
            logger.warning("Cannot auto-join meeting: Graph client not configured")
            return

        from app.config import settings as app_settings
        callback_host = app_settings.callback_host if app_settings.callback_host else None
        if not callback_host:
            callback_host = turn_context.activity.service_url or "http://localhost:8000"
        callback_url = f"{callback_host.rstrip('/')}/api/calling"

        thread_id = turn_context.activity.conversation.id
        if ";" in thread_id:
            thread_id = thread_id.split(";")[0]

        try:
            logger.info(f"Auto-joining meeting: {join_url}")
            result = await self.graph_client.join_meeting_by_url(callback_url, join_url, thread_id)
            call_id = result.get("id", "")

            # Extract meeting subject if available
            chan_data = turn_context.activity.channel_data or {}
            meeting_subject = chan_data.get("meeting", {}).get("title", "")

            conv_ref = {
                "service_url": turn_context.activity.service_url,
                "conversation_id": turn_context.activity.conversation.id,
            }
            self._pending_join_context[call_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "meeting_subject": meeting_subject,
                "conversation_reference": conv_ref,
            }

            await turn_context.send_activity(
                "I've joined the meeting automatically. "
                "I'll send you suggested responses as the conversation unfolds."
            )
        except Exception as e:
            logger.error(f"Auto-join meeting failed: {e}", exc_info=True)
            await turn_context.send_activity(
                f"I detected a meeting but couldn't join automatically: {str(e)[:200]}\n\n"
                "You can try manually with `/join <meeting-url>`."
            )

    async def on_event_activity(self, turn_context: TurnContext):
        """Handle Teams meeting lifecycle events (meetingStart, meetingEnd)."""
        event_name = turn_context.activity.name or ""
        chan_data = turn_context.activity.channel_data or {}

        logger.info(f"Event received: {event_name}, channel_data keys: {list(chan_data.keys())}")

        if "meetingStart" in event_name:
            join_url = self._extract_meeting_join_url(turn_context)
            if join_url:
                user_id = (
                    turn_context.activity.from_property.id
                    if turn_context.activity.from_property
                    else "unknown"
                )
                user_name = (
                    turn_context.activity.from_property.name
                    if turn_context.activity.from_property
                    else "User"
                ) or "User"
                await self._auto_join_meeting(turn_context, join_url, user_id, user_name)
            else:
                logger.info("meetingStart event but no joinUrl found in channel_data")

        elif "meetingEnd" in event_name:
            # Clean up any sessions associated with this conversation
            conv_id = turn_context.activity.conversation.id
            if self.meeting_service:
                for call_id, session in list(self.meeting_service.active_sessions.items()):
                    if session.conversation_reference.get("conversation_id") == conv_id:
                        self.meeting_service.end_session(call_id)
                        logger.info(f"Meeting ended, session {call_id} cleaned up")

    async def on_members_added_activity(self, members_added, turn_context: TurnContext):
        bot_id = turn_context.activity.recipient.id
        bot_was_added = any(m.id == bot_id for m in members_added)

        # Check if this is a meeting chat (bot invited to a meeting)
        if bot_was_added:
            join_url = self._extract_meeting_join_url(turn_context)
            if join_url:
                # Bot was added to a meeting -- auto-join
                chan_data = turn_context.activity.channel_data or {}
                # The user who added the bot is typically the organizer
                user_id = (
                    turn_context.activity.from_property.id
                    if turn_context.activity.from_property
                    else "unknown"
                )
                user_name = (
                    turn_context.activity.from_property.name
                    if turn_context.activity.from_property
                    else "User"
                ) or "User"
                await self._auto_join_meeting(turn_context, join_url, user_id, user_name)
                return

        # Regular welcome for non-meeting chats
        for member in members_added:
            if member.id != bot_id:
                await turn_context.send_activity(
                    "**Welcome to MyAi!**\n\n"
                    "I'm your local AI assistant, powered by Ollama. "
                    "I run entirely on your machine -- your data stays private.\n\n"
                    "Type `/help` to see what I can do, or just start chatting!"
                )
