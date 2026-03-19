from __future__ import annotations

import logging
import re
from pathlib import Path

from app.agent.core import AgentCore
from app.config import permissions_config
from app.security.permissions import auth_service, permission_manager
from app.services.graph import GraphClient
from app.services.meeting_transcript import MeetingTranscriptService
from app.services.web_search import WebSearchService
from app.storage.database import Database

logger = logging.getLogger(__name__)


class SlackBot:
    """Slack bot that routes messages to the MyAi agent."""

    def __init__(
        self,
        agent: AgentCore,
        search_service: WebSearchService,
        meeting_service: MeetingTranscriptService | None = None,
        database: Database | None = None,
        graph_client: GraphClient | None = None,
    ):
        self.agent = agent
        self.search_service = search_service
        self.meeting_service = meeting_service
        self.database = database
        self.graph_client = graph_client

    async def handle_message(self, body: dict, say, client=None) -> None:
        """Handle a direct message or channel message."""
        event = body.get("event", {})
        user_id = event.get("user", "")
        text = (event.get("text") or "").strip()
        channel = event.get("channel", "")

        # Ignore bot messages
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return

        if not text:
            return

        # Auth check
        if not auth_service.is_user_allowed(user_id):
            await say("You are not authorized to use MyAi.", channel=channel)
            return

        # Resolve display name
        user_name = await self._get_user_name(client, user_id)

        # Check for slash-style commands (text starting with /)
        if text.startswith("/"):
            response = await self._handle_command(text, user_id, user_name, channel, say, client)
            if response is not None:
                if response:
                    await say(response, channel=channel)
                return

        # Process through agent
        try:
            result = await self.agent.process_message(user_id, text)
            response = result["text"]
        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            response = f"Something went wrong: {str(e)[:200]}"

        # Slack has a ~4000 char soft limit per message
        if len(response) > 3900:
            chunks = [response[i:i + 3900] for i in range(0, len(response), 3900)]
            for chunk in chunks:
                await say(chunk, channel=channel)
        else:
            await say(response, channel=channel)

    async def handle_app_mention(self, body: dict, say, client=None) -> None:
        """Handle @MyAi mentions in channels."""
        event = body.get("event", {})
        user_id = event.get("user", "")
        text = (event.get("text") or "").strip()
        channel = event.get("channel", "")

        # Strip the bot mention from the text
        text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

        if not text:
            await say("Hey! I'm MyAi. Send me a message or type `/help` to see what I can do.", channel=channel)
            return

        if not auth_service.is_user_allowed(user_id):
            await say("You are not authorized to use MyAi.", channel=channel)
            return

        user_name = await self._get_user_name(client, user_id)

        # Check for commands
        if text.startswith("/"):
            response = await self._handle_command(text, user_id, user_name, channel, say, client)
            if response is not None:
                if response:
                    await say(response, channel=channel)
                return

        try:
            result = await self.agent.process_message(user_id, text)
            response = result["text"]
        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            response = f"Something went wrong: {str(e)[:200]}"

        if len(response) > 3900:
            chunks = [response[i:i + 3900] for i in range(0, len(response), 3900)]
            for chunk in chunks:
                await say(chunk, channel=channel)
        else:
            await say(response, channel=channel)

    async def handle_slash_command(self, ack, body: dict, say, client=None) -> None:
        """Handle Slack slash commands (e.g. /myai help)."""
        await ack()
        user_id = body.get("user_id", "")
        text = (body.get("text") or "").strip()
        channel = body.get("channel_id", "")

        if not auth_service.is_user_allowed(user_id):
            await say("You are not authorized to use MyAi.", channel=channel)
            return

        user_name = await self._get_user_name(client, user_id)

        # Prefix with / so command handler recognizes it
        cmd_text = f"/{text}" if text and not text.startswith("/") else text or "/help"
        response = await self._handle_command(cmd_text, user_id, user_name, channel, say, client)
        if response is not None:
            if response:
                await say(response, channel=channel)
        else:
            # Not a command, treat as agent message
            try:
                result = await self.agent.process_message(user_id, text)
                response = result["text"]
            except Exception as e:
                logger.error(f"Agent error: {e}", exc_info=True)
                response = f"Something went wrong: {str(e)[:200]}"
            await say(response, channel=channel)

    # ── Command handling ──

    async def _handle_command(
        self, text: str, user_id: str, user_name: str, channel: str, say, client=None
    ) -> str | None:
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if command == "/help":
            return (
                "*MyAi Commands*\n\n"
                "*General:*\n"
                "- `/model <name>` -- Switch Ollama model\n"
                "- `/status` -- Show current config and health\n"
                "- `/skills` -- Show available enterprise AI agents\n"
                "- `/clear` -- Clear conversation history\n"
                "- `/help` -- Show this message\n\n"
                "*Profile & Context:*\n"
                "- `/profile <info>` -- Set your profile (name, role, bio)\n"
                "- `/context add <name> <content>` -- Add project/topic knowledge\n"
                "- `/context list` -- Show stored contexts\n"
                "- `/context remove <name>` -- Remove a context\n\n"
                "*Files & Search:*\n"
                "- `/allow <path>` -- Grant file access to a directory\n"
                "- `/revoke` -- Revoke all file permissions\n"
                "- `/search on|off` -- Toggle web search\n"
                "- `/index <path>` -- Index a directory for RAG\n\n"
                "*Microsoft 365:*\n"
                "- `/connect` -- Sign in to Microsoft 365\n"
                "- `/disconnect` -- Sign out of Microsoft 365\n"
                "- `/calendar [days]` -- View upcoming calendar events\n"
                "- `/email [count]` -- View recent emails\n"
                "- `/files [search]` -- Browse or search OneDrive files\n\n"
                "*Enterprise Skills (auto-routed):*\n"
                "Just ask naturally — MyAi routes to the right specialist:\n"
                "_\"Reset my password\" → VULCAN (IT) | \"How many PTO days?\" → VESTA (HR)_\n"
                "_\"Submit expense report\" → MIDAS (Finance) | \"Review this NDA\" → MINERVA (Legal)_"
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

            search_status = "On" if permission_manager.is_search_enabled(user_id) else "Off"
            dirs = permissions_config.allowed_dirs or ["None"]

            sessions_info = ""
            if self.meeting_service:
                active = self.meeting_service.active_sessions
                if active:
                    sessions_info = f"\n*Active transcript sessions:* {len(active)}"

            graph_status = "Not configured"
            if self.graph_client and self.graph_client.is_configured:
                if self.graph_client.is_user_connected(user_id):
                    email = self.graph_client.get_user_email(user_id)
                    graph_status = f"Connected ({email})" if email else "Connected"
                else:
                    graph_status = "Configured (not signed in)"

            return (
                f"*MyAi Status*\n\n"
                f"*Ollama:* {'Connected' if ollama_ok else 'Not reachable'}\n"
                f"*Model:* `{self.agent.ollama.model}`\n"
                f"*Available models:* {', '.join(models) or 'N/A'}\n"
                f"*Web search:* {search_status}\n"
                f"*Microsoft 365:* {graph_status}\n"
                f"*Allowed dirs:* {chr(10).join(dirs)}\n"
                f"*User:* {user_name} (`{user_id}`)"
                f"{sessions_info}"
            )

        elif command == "/model":
            if not arg:
                return "Usage: `/model <model_name>` (e.g., `/model mistral:7b`)"
            self.agent.ollama.set_model(arg)
            return f"Switched to model: `{arg}`"

        elif command == "/allow":
            if not arg:
                return "Usage: `/allow <directory_path>` (e.g., `/allow /home/user/projects`)"
            resolved = str(Path(arg).resolve())
            if not Path(resolved).exists():
                return f"Directory not found: `{arg}`"
            if not Path(resolved).is_dir():
                return f"Not a directory: `{arg}`"
            permissions_config.grant_directory(resolved)
            permission_manager.grant(user_id, f"dir:{resolved}")
            return f"Granted access to: `{resolved}`"

        elif command == "/revoke":
            permissions_config.revoke_all()
            permission_manager.revoke_all(user_id)
            return "All file permissions revoked."

        elif command == "/search":
            if arg.lower() in ("on", "true", "enable", "1"):
                self.search_service.toggle(True)
                permission_manager.set_search_enabled(user_id, True)
                return "Web search *enabled*. The agent can now search the web when needed."
            elif arg.lower() in ("off", "false", "disable", "0"):
                self.search_service.toggle(False)
                permission_manager.set_search_enabled(user_id, False)
                return "Web search *disabled*."
            else:
                return "Usage: `/search on` or `/search off`"

        elif command == "/index":
            if not arg:
                return "Usage: `/index <directory_path>`"
            resolved = str(Path(arg).resolve())
            if not permissions_config.is_path_allowed(resolved):
                return f"Directory not in allowlist. Run `/allow {arg}` first."
            try:
                result = await self.agent.tools.rag_service.index_directory(resolved)
                return f"Indexing complete! {result}"
            except Exception as e:
                return f"Indexing failed: {e}"

        elif command == "/clear":
            await self.agent.db.clear_conversation(user_id)
            return "Conversation history cleared."

        elif command == "/profile":
            if not self.database:
                return "Database not configured."
            if not arg:
                profile = await self.database.get_user_profile(user_id)
                if profile and any(profile.get(k) for k in ("name", "role", "bio")):
                    return (
                        f"*Your Profile*\n\n"
                        f"*Name:* {profile.get('name') or '(not set)'}\n"
                        f"*Role:* {profile.get('role') or '(not set)'}\n"
                        f"*Bio:* {profile.get('bio') or '(not set)'}\n\n"
                        "Update with: `/profile name:<your name> role:<your role> bio:<about you>`"
                    )
                return (
                    "No profile set yet. Set one with:\n\n"
                    "`/profile name:Anubhav role:Software Engineer bio:I work on frontend and API integrations`"
                )

            name = role = bio = ""
            name_m = re.search(r'name:\s*([^|]+?)(?=\s+(?:role|bio):|$)', arg)
            role_m = re.search(r'role:\s*([^|]+?)(?=\s+(?:name|bio):|$)', arg)
            bio_m = re.search(r'bio:\s*(.+)', arg)
            if name_m:
                name = name_m.group(1).strip()
            if role_m:
                role = role_m.group(1).strip()
            if bio_m:
                bio = bio_m.group(1).strip()

            if not name and not role and not bio:
                bio = arg.strip()

            await self.database.set_user_profile(user_id, name=name or user_name, role=role, bio=bio)
            profile = await self.database.get_user_profile(user_id)
            return (
                f"Profile updated!\n\n"
                f"*Name:* {profile.get('name', '')}\n"
                f"*Role:* {profile.get('role', '')}\n"
                f"*Bio:* {profile.get('bio', '')}\n\n"
                "This info will be used when suggesting responses in meetings."
            )

        elif command == "/context":
            if not self.database:
                return "Database not configured."
            if not arg:
                return (
                    "Usage:\n"
                    "- `/context add <name> <content>` -- Add knowledge\n"
                    "- `/context list` -- Show all stored contexts\n"
                    "- `/context remove <name>` -- Remove a context"
                )

            sub_parts = arg.split(maxsplit=1)
            sub_cmd = sub_parts[0].lower()
            sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""

            if sub_cmd == "list":
                contexts = await self.database.get_all_contexts(user_id)
                if not contexts:
                    return "No contexts stored. Add one with `/context add <name> <content>`"
                lines = ["*Stored Contexts:*\n"]
                for ctx in contexts:
                    preview = ctx["content"][:100].replace("\n", " ")
                    lines.append(f"- *{ctx['name']}*: {preview}...")
                return "\n".join(lines)

            elif sub_cmd == "add":
                name_and_content = sub_arg.split(maxsplit=1)
                if len(name_and_content) < 2:
                    return "Usage: `/context add <name> <content>`\nExample: `/context add myproject We are building a Slack bot...`"
                ctx_name = name_and_content[0]
                content = name_and_content[1]
                await self.database.add_context(user_id, ctx_name, content)
                return f"Context *{ctx_name}* saved ({len(content)} chars). This will be used in meeting suggestions."

            elif sub_cmd == "remove":
                if not sub_arg:
                    return "Usage: `/context remove <name>`"
                removed = await self.database.remove_context(user_id, sub_arg)
                if removed:
                    return f"Context *{sub_arg}* removed."
                return f"No context named *{sub_arg}* found."

            return "Unknown subcommand. Use `add`, `list`, or `remove`."

        elif command == "/skills":
            if hasattr(self.agent, 'skill_registry') and self.agent.skill_registry:
                return self.agent.skill_registry.get_skills_summary()
            return "No enterprise skills configured."

        elif command == "/transcript":
            return await self._handle_transcript_command(arg, user_id, user_name, channel, say, client)

        elif command == "/connect":
            return await self._handle_connect(user_id)

        elif command == "/disconnect":
            return self._handle_disconnect(user_id)

        elif command == "/calendar":
            return await self._handle_calendar(arg, user_id)

        elif command == "/email":
            return await self._handle_email(arg, user_id)

        elif command == "/files":
            return await self._handle_files(arg, user_id)

        return None  # Not a recognized command -- pass to agent

    # ── Transcript session management ──

    async def _handle_transcript_command(
        self, arg: str, user_id: str, user_name: str, channel: str, say, client=None
    ) -> str:
        if not self.meeting_service:
            return "Meeting service not configured."

        sub_parts = arg.split(maxsplit=1) if arg else [""]
        sub_cmd = sub_parts[0].lower()
        sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""

        if sub_cmd == "start":
            # Check if user already has a session
            existing = self.meeting_service.get_session_by_user(user_id)
            if existing:
                return (
                    f"You already have an active transcript session (id: `{existing.call_id[:12]}...`).\n"
                    "End it first with `/transcript end`."
                )

            import uuid
            session_id = str(uuid.uuid4())
            meeting_subject = sub_arg or "Meeting"

            self.meeting_service.start_session(
                call_id=session_id,
                user_id=user_id,
                user_name=user_name,
                meeting_subject=meeting_subject,
                conversation_reference={
                    "channel_id": channel,
                },
            )
            return (
                f"Transcript session started: *{meeting_subject}*\n\n"
                "Now paste transcript text with `/transcript paste <text>`\n"
                "I'll analyze it and send you suggested responses.\n\n"
                "When done, run `/transcript end` to save a summary."
            )

        elif sub_cmd == "paste":
            if not sub_arg:
                return "Usage: `/transcript paste <transcript text>`"

            session = self.meeting_service.get_session_by_user(user_id)
            if not session:
                return "No active transcript session. Start one with `/transcript start [subject]`"

            await self.meeting_service.ingest_transcript(session.call_id, sub_arg)
            line_count = len(session.transcript_lines)
            return f"Ingested transcript text ({line_count} total lines). Analyzing..."

        elif sub_cmd == "end":
            session = self.meeting_service.get_session_by_user(user_id)
            if not session:
                return "No active transcript session."

            line_count = len(session.transcript_lines)
            self.meeting_service.end_session(session.call_id)
            return (
                f"Transcript session ended. ({line_count} lines captured)\n"
                "A meeting summary will be saved automatically."
            )

        elif sub_cmd == "status":
            session = self.meeting_service.get_session_by_user(user_id)
            if not session:
                return "No active transcript session."
            return (
                f"*Active Session*\n"
                f"*Subject:* {session.meeting_subject}\n"
                f"*Lines:* {len(session.transcript_lines)}\n"
                f"*Last suggestion:* {(session.last_suggestion[:150] + '...') if session.last_suggestion else '(none yet)'}"
            )

        else:
            return (
                "Usage:\n"
                "- `/transcript start [subject]` -- Start a transcript session\n"
                "- `/transcript paste <text>` -- Feed transcript text\n"
                "- `/transcript status` -- Check session status\n"
                "- `/transcript end` -- End session and save summary"
            )

    # ── Microsoft Graph commands ──

    async def _handle_connect(self, user_id: str) -> str:
        if not self.graph_client or not self.graph_client.is_configured:
            return "Microsoft 365 integration is not configured. Set `GRAPH_CLIENT_ID` and `GRAPH_CLIENT_SECRET` in `.env`."
        if self.graph_client.is_user_connected(user_id):
            email = self.graph_client.get_user_email(user_id)
            return f"You're already connected as *{email}*. Use `/disconnect` to sign out first."
        auth_url = self.graph_client.get_auth_url(state=user_id)
        return (
            "*Connect to Microsoft 365*\n\n"
            f"<{auth_url}|Click here to sign in with your Microsoft account>\n\n"
            "After signing in, you'll be redirected back and your calendar, email, "
            "and files will be accessible through MyAi."
        )

    def _handle_disconnect(self, user_id: str) -> str:
        if not self.graph_client:
            return "Microsoft 365 integration is not configured."
        if not self.graph_client.is_user_connected(user_id):
            return "You're not connected to Microsoft 365."
        self.graph_client.disconnect_user(user_id)
        return "Disconnected from Microsoft 365. Your tokens have been cleared."

    async def _handle_calendar(self, arg: str, user_id: str) -> str:
        if not self.graph_client or not self.graph_client.is_configured:
            return "Microsoft 365 integration is not configured."
        if not self.graph_client.is_user_connected(user_id):
            return "Not connected to Microsoft 365. Use `/connect` first."

        try:
            days = 7
            top = 10
            if arg:
                try:
                    days = int(arg)
                except ValueError:
                    pass
            events = await self.graph_client.get_calendar_events(user_id, top=top, days_ahead=days)
            if not events:
                return f"No upcoming events in the next {days} days."

            lines = [f"*Upcoming Calendar ({days} days):*\n"]
            for e in events:
                start = e["start"][:16].replace("T", " ") if e["start"] else "?"
                subject = e["subject"]
                location = f" | {e['location']}" if e.get("location") else ""
                online = " (Online)" if e.get("is_online") else ""
                lines.append(f"- *{start}* — {subject}{location}{online}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Calendar fetch failed: {e}", exc_info=True)
            return f"Failed to fetch calendar: {str(e)[:200]}"

    async def _handle_email(self, arg: str, user_id: str) -> str:
        if not self.graph_client or not self.graph_client.is_configured:
            return "Microsoft 365 integration is not configured."
        if not self.graph_client.is_user_connected(user_id):
            return "Not connected to Microsoft 365. Use `/connect` first."

        try:
            top = 10
            if arg:
                try:
                    top = int(arg)
                except ValueError:
                    pass
            emails = await self.graph_client.get_recent_emails(user_id, top=top)
            if not emails:
                return "No recent emails."

            lines = ["*Recent Emails:*\n"]
            for e in emails:
                read_icon = "" if e["is_read"] else " *NEW*"
                importance = " (!)" if e["importance"] == "high" else ""
                received = e["received"][:16].replace("T", " ") if e["received"] else ""
                lines.append(
                    f"- {received} — *{e['subject']}*{read_icon}{importance}\n"
                    f"  From: {e['from']} | {e['preview'][:80]}..."
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Email fetch failed: {e}", exc_info=True)
            return f"Failed to fetch emails: {str(e)[:200]}"

    async def _handle_files(self, arg: str, user_id: str) -> str:
        if not self.graph_client or not self.graph_client.is_configured:
            return "Microsoft 365 integration is not configured."
        if not self.graph_client.is_user_connected(user_id):
            return "Not connected to Microsoft 365. Use `/connect` first."

        try:
            if arg:
                files = await self.graph_client.search_files(user_id, arg)
                header = f"*Files matching \"{arg}\":*\n"
            else:
                files = await self.graph_client.get_recent_files(user_id)
                header = "*Recent Files:*\n"

            if not files:
                return "No files found."

            lines = [header]
            for f in files:
                size_kb = f["size"] / 1024 if f["size"] else 0
                modified = f["modified"][:16].replace("T", " ") if f.get("modified") else ""
                lines.append(f"- *{f['name']}* ({size_kb:.0f} KB) — {modified}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Files fetch failed: {e}", exc_info=True)
            return f"Failed to fetch files: {str(e)[:200]}"

    # ── Helpers ──

    async def _get_user_name(self, client, user_id: str) -> str:
        """Resolve Slack user ID to display name."""
        if not client:
            return "User"
        try:
            result = await client.users_info(user=user_id)
            profile = result.get("user", {}).get("profile", {})
            return (
                profile.get("display_name")
                or profile.get("real_name")
                or result.get("user", {}).get("name", "User")
            )
        except Exception:
            return "User"
