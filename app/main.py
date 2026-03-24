"""MyAi -- Local AI Agent for Slack + Web UI.

Run with: python -m app.main           (Slack + Web UI)
          python -m app.main --web-only (Web UI only, no Slack)
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import logging
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

from app.config import settings
from app.agent.core import AgentCore
from app.auth.service import AuthService as AuthSessionService
from app.auth.rbac import RBACService
from app.bot import SlackBot
from app.services.ollama import OllamaClient
from app.services.rag import RAGService
from app.services.meeting_transcript import MeetingTranscriptService
from app.services.graph import GraphClient
from app.admin.analytics import AnalyticsService
from app.admin.routes import setup_admin_routes
from app.admin.datasource_routes import setup_datasource_routes
from app.services.doc_processor import DocumentProcessor
from app.services.encryption import ConfigEncryption
from app.services.indexing import IndexingService
from app.services.nexgai_client import NexgAIClient
from app.services.web_search import WebSearchService
from app.services.file_access import FileAccessService
from app.agent.tools import ToolRegistry
from app.learning.feedback_service import FeedbackService
from app.learning.engine import LearningEngine
from app.learning.routes import setup_learning_routes
from app.storage.database import Database
from app.services.file_watcher import FileWatcherService
from app.services.reminders import ReminderService
from app.services.whatsapp import WhatsAppService

# -- Logging --
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("miai")

# -- Initialize services --
ollama_client = OllamaClient()
rag_service = RAGService(ollama_client)
database = Database(settings.database_path)
graph_client = GraphClient()

# -- Initialize auth services --
auth_service = AuthSessionService(db_path=settings.database_path)
rbac_service = RBACService(db_path=settings.database_path)

# -- Initialize Phase 3 services --
encryption = ConfigEncryption()
doc_processor = DocumentProcessor()
indexing_service = IndexingService(database, rag_service, doc_processor, encryption)

# -- Initialize NexgAI integration --
nexgai_client = NexgAIClient()

# -- Initialize web search and file access --
search_service = WebSearchService()
file_service = FileAccessService()

# -- Initialize file watcher and reminders --
file_watcher = FileWatcherService()
reminder_service = ReminderService()
whatsapp_service = WhatsAppService()
tool_registry = ToolRegistry(file_service, search_service, rag_service)
tool_registry._reminder_service = reminder_service

# -- Initialize agent (NexgAI agents + Ollama LLM with tools) --
agent = AgentCore(
    ollama_client, database,
    nexgai_client=nexgai_client if nexgai_client.is_configured else None,
    tools=tool_registry,
)
agent.rbac_service = rbac_service

# -- Initialize Phase 4: Self-Learning Loop --
feedback_service = FeedbackService(database)
learning_engine = LearningEngine(database, ollama_client)

# -- Bot instance (created before Slack app so simulate endpoints can use meeting_service) --
# Slack app reference is set later in run_async()
_slack_app = None


async def _deliver_suggestion(session, suggestion: str) -> None:
    """Deliver a meeting suggestion to the user via Slack message."""
    ref = session.conversation_reference
    channel_id = ref.get("channel_id")
    if not channel_id:
        logger.warning("Cannot deliver suggestion: missing channel_id in conversation reference")
        return

    formatted = (
        f"*Meeting Suggestion*\n\n"
        f"_{suggestion}_"
    )

    if _slack_app is None:
        logger.warning("Slack app not initialized yet, cannot deliver suggestion")
        return

    try:
        await _slack_app.client.chat_postMessage(channel=channel_id, text=formatted)
    except Exception as e:
        logger.error(f"Failed to send suggestion to Slack: {e}", exc_info=True)


# -- Initialize meeting transcript service --
meeting_service = MeetingTranscriptService(
    ollama=ollama_client,
    deliver_fn=_deliver_suggestion,
    database=database,
)

bot = SlackBot(agent, search_service, meeting_service, database, graph_client=graph_client)


# -- Debug HTTP server (for simulate script and health checks) --
async def health(req: web.Request) -> web.Response:
    """Health check endpoint."""
    ollama_ok = await ollama_client.health_check()
    nexgai_ok = await nexgai_client.health_check() if nexgai_client.is_configured else None
    status = "ok" if ollama_ok else "degraded"
    result = {
        "status": status,
        "ollama": "connected" if ollama_ok else "unreachable",
        "model": ollama_client.model,
        "platform": "slack",
    }
    if nexgai_ok is not None:
        result["nexgai"] = "connected" if nexgai_ok else "unreachable"
        result["nexgai_circuit_breaker"] = "open" if nexgai_client.circuit_breaker.is_open else "closed"
    return web.json_response(result)


async def simulate_transcript(req: web.Request) -> web.Response:
    """Dev-only endpoint: inject transcript text and immediately generate a suggestion."""
    try:
        body = await req.json()
        transcript_text = body.get("transcript_text", "")
        if not transcript_text:
            return web.json_response({"error": "transcript_text is required"}, status=400)

        sessions = meeting_service.active_sessions
        if not sessions:
            return web.json_response({"error": "No active meeting session"}, status=404)

        results = []
        for call_id, session in sessions.items():
            new_lines = meeting_service._parse_transcript_text(transcript_text)
            if new_lines:
                session.transcript_lines.extend(new_lines)

            session.last_suggestion_hash = ""
            session.last_suggestion_time = 0.0

            suggestion = await meeting_service.generate_and_deliver(session)
            results.append({
                "call_id": call_id,
                "lines_added": len(new_lines) if new_lines else 0,
                "total_lines": len(session.transcript_lines),
                "suggestion": suggestion or "(no suggestion generated)",
                "conversation_ref": session.conversation_reference,
            })

        return web.json_response({"status": "ok", "results": results})
    except Exception as e:
        logger.error(f"Simulate transcript error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def debug_sessions(req: web.Request) -> web.Response:
    """Dev-only endpoint: inspect active meeting sessions."""
    sessions = meeting_service.active_sessions
    data = []
    for call_id, s in sessions.items():
        data.append({
            "call_id": call_id,
            "user_name": s.user_name,
            "user_role": s.user_role,
            "meeting_subject": s.meeting_subject,
            "conversation_reference": s.conversation_reference,
            "transcript_line_count": len(s.transcript_lines),
            "last_suggestion": s.last_suggestion[:200] if s.last_suggestion else None,
        })
    return web.json_response({"active_sessions": data})


async def auth_callback(req: web.Request) -> web.Response:
    """OAuth2 callback for Microsoft Graph delegated auth."""
    code = req.query.get("code")
    state = req.query.get("state", "")  # slack_user_id
    error = req.query.get("error")

    if error:
        error_desc = req.query.get("error_description", "Unknown error")
        logger.error(f"Graph OAuth error: {error} — {error_desc}")
        return web.Response(
            text=f"<html><body><h2>Authentication Failed</h2><p>{error_desc}</p>"
            "<p>You can close this window.</p></body></html>",
            content_type="text/html",
        )

    if not code or not state:
        return web.Response(
            text="<html><body><h2>Missing parameters</h2>"
            "<p>code and state are required.</p></body></html>",
            content_type="text/html",
            status=400,
        )

    try:
        tokens = await graph_client.exchange_code(code, state)
        email = tokens.user_email or "your Microsoft account"
        logger.info(f"Graph OAuth success for Slack user {state} ({email})")

        # Notify the user in Slack
        if _slack_app:
            try:
                # Open a DM with the user and send confirmation
                dm = await _slack_app.client.conversations_open(users=state)
                channel = dm["channel"]["id"]
                await _slack_app.client.chat_postMessage(
                    channel=channel,
                    text=(
                        f"*Connected to Microsoft 365!*\n\n"
                        f"Signed in as: *{email}*\n\n"
                        "You can now use:\n"
                        "- `/calendar` — View upcoming events\n"
                        "- `/email` — View recent emails\n"
                        "- `/files` — Browse OneDrive files\n"
                        "- Ask EKLAVYA to schedule meetings or draft emails naturally"
                    ),
                )
            except Exception as e:
                logger.error(f"Failed to notify user in Slack: {e}")

        return web.Response(
            text=(
                f"<html><body>"
                f"<h2>Connected to Microsoft 365!</h2>"
                f"<p>Signed in as <strong>{email}</strong>.</p>"
                f"<p>You can close this window and return to Slack.</p>"
                f"</body></html>"
            ),
            content_type="text/html",
        )
    except Exception as e:
        logger.error(f"Graph OAuth code exchange failed: {e}", exc_info=True)
        return web.Response(
            text=f"<html><body><h2>Authentication Failed</h2><p>{str(e)[:300]}</p>"
            "<p>Please try again with /connect.</p></body></html>",
            content_type="text/html",
            status=500,
        )


# ── Auth API Endpoints ──

async def auth_setup_status(req: web.Request) -> web.Response:
    """Check if initial setup is complete."""
    try:
        complete = await auth_service.is_setup_complete()
        return web.json_response({"setup_complete": complete})
    except Exception as e:
        logger.error(f"Setup status check error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def auth_setup(req: web.Request) -> web.Response:
    """Create initial super admin. Only works when no super_admin exists."""
    try:
        complete = await auth_service.is_setup_complete()
        if complete:
            return web.json_response(
                {"error": "Setup already complete. A super admin already exists."},
                status=400,
            )

        body = await req.json()
        email = (body.get("email") or "").strip()
        display_name = (body.get("display_name") or "").strip()
        password = body.get("password", "")

        if not email or not display_name or not password:
            return web.json_response(
                {"error": "email, display_name, and password are required"},
                status=400,
            )

        if len(password) < 6:
            return web.json_response(
                {"error": "Password must be at least 6 characters"},
                status=400,
            )

        from app.auth.models import RoleLevel
        user = await auth_service.create_user(
            email=email,
            display_name=display_name,
            password=password,
            role_level=RoleLevel.SUPER_ADMIN,
        )

        # Auto-login the newly created admin
        session = await auth_service.authenticate(email, password)

        return web.json_response({
            "user": user.to_dict(),
            "token": session.token if session else None,
        })
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        logger.error(f"Setup error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def auth_login(req: web.Request) -> web.Response:
    """Login with email/password."""
    try:
        body = await req.json()
        email = (body.get("email") or "").strip()
        password = body.get("password", "")

        if not email or not password:
            return web.json_response(
                {"error": "email and password are required"},
                status=400,
            )

        session = await auth_service.authenticate(email, password)
        if not session:
            return web.json_response(
                {"error": "Invalid email or password"},
                status=401,
            )

        user = await auth_service.get_user(session.user_id)
        return web.json_response({
            "token": session.token,
            "user": user.to_dict() if user else None,
        })
    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def auth_logout(req: web.Request) -> web.Response:
    """Invalidate session."""
    try:
        token = _extract_token(req)
        if token:
            await auth_service.logout(token)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"Logout error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def auth_me(req: web.Request) -> web.Response:
    """Get current user from token."""
    try:
        token = _extract_token(req)
        if not token:
            return web.json_response({"error": "No token provided"}, status=401)

        user = await auth_service.validate_session(token)
        if not user:
            return web.json_response({"error": "Invalid or expired token"}, status=401)

        return web.json_response({"user": user.to_dict()})
    except Exception as e:
        logger.error(f"Auth me error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


def _extract_token(req: web.Request) -> str | None:
    """Extract auth token from Authorization header or query param."""
    auth_header = req.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return req.query.get("token")


# -- WebSocket chat handler (Web UI) --
_ws_clients: dict[str, web.WebSocketResponse] = {}  # user_id -> ws


async def websocket_handler(req: web.Request) -> web.WebSocketResponse:
    """Handle WebSocket connections from the Web UI."""
    ws = web.WebSocketResponse()
    await ws.prepare(req)

    user_id = "web-user-anon"
    user_name = "User"
    auth_user = None  # Will hold the authenticated User object
    active_conversation_id = None  # Track which conversation the user is chatting in
    briefing_shown = False  # Only show briefing once per WebSocket session

    logger.info(f"WebSocket client connected from {req.remote}")

    # Background task: periodically check for file notifications
    async def _file_notification_loop():
        """Send file watcher notifications every 30 seconds."""
        try:
            while not ws.closed:
                await asyncio.sleep(30)
                if ws.closed:
                    break
                try:
                    file_notifs = file_watcher.get_pending_notifications()
                    if file_notifs:
                        notif_msg = file_watcher.format_notifications_message(file_notifs)
                        await ws.send_json({
                            "type": "system",
                            "text": notif_msg,
                            "source": "file_watcher",
                        })
                except Exception as e:
                    logger.debug(f"File notification periodic check error: {e}")
        except asyncio.CancelledError:
            pass

    file_notif_task = asyncio.create_task(_file_notification_loop())

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "text": "Invalid JSON"})
                    continue

                msg_type = data.get("type", "message")

                if msg_type == "auth":
                    # Try token-based auth first
                    token = data.get("token")
                    if token:
                        validated_user = await auth_service.validate_session(token)
                        if validated_user:
                            auth_user = validated_user
                            user_id = validated_user.id
                            user_name = validated_user.display_name
                            _ws_clients[user_id] = ws
                            logger.info(f"WebSocket auth (token): {user_name} ({user_id}) role={validated_user.role_level.value}")
                            await ws.send_json({
                                "type": "system",
                                "text": f"Connected as {user_name}",
                                "user": validated_user.to_dict(),
                            })

                            # Auto-briefing on login (only once per session)
                            if not briefing_shown:
                                briefing_shown = True
                                try:
                                    from app.services.briefing import generate_briefing
                                    briefing = await generate_briefing(
                                        user_name=user_name,
                                        user_id=user_id,
                                        ollama=ollama_client,
                                        database=database,
                                    )
                                    if briefing:
                                        await ws.send_json({
                                            "type": "response",
                                            "text": briefing,
                                            "source": "briefing",
                                        })
                                except Exception as e:
                                    logger.warning(f"Briefing generation failed: {e}")

                            # Check for any pending file notifications
                            try:
                                file_notifs = file_watcher.get_pending_notifications()
                                if file_notifs:
                                    notif_msg = file_watcher.format_notifications_message(file_notifs)
                                    await ws.send_json({
                                        "type": "system",
                                        "text": notif_msg,
                                        "source": "file_watcher",
                                    })
                            except Exception as e:
                                logger.warning(f"File notification check failed: {e}")

                            continue
                        else:
                            await ws.send_json({
                                "type": "auth_error",
                                "text": "Invalid or expired token. Please log in again.",
                            })
                            continue

                    # Fallback: legacy self-declared auth (backward compat)
                    user_id = data.get("user_id", user_id)
                    user_name = data.get("user_name", user_name)
                    _ws_clients[user_id] = ws
                    logger.info(f"WebSocket auth (legacy): {user_name} ({user_id})")
                    await ws.send_json({
                        "type": "system",
                        "text": f"Connected as {user_name}",
                    })
                    continue

                if msg_type == "feedback":
                    # Handle thumbs up/down feedback on a message
                    try:
                        fb_id = await feedback_service.submit(
                            message_id=data.get("message_id", 0),
                            conversation_id=data.get("conversation_id", ""),
                            user_id=user_id,
                            rating=data.get("rating", ""),
                            comment=data.get("comment", ""),
                            source=data.get("source", "local"),
                            agent_name=data.get("agent_name"),
                        )
                        await ws.send_json({
                            "type": "feedback_ack",
                            "message_id": data.get("message_id"),
                            "rating": data.get("rating"),
                            "feedback_id": fb_id,
                        })
                    except Exception as e:
                        await ws.send_json({
                            "type": "error",
                            "text": f"Feedback error: {str(e)[:200]}",
                        })
                    continue

                if msg_type == "switch_conversation":
                    conv_id = data.get("conversation_id")
                    if conv_id:
                        # Verify user owns this conversation
                        owner = await database.get_conversation_owner(conv_id)
                        if owner == user_id:
                            active_conversation_id = conv_id
                            await ws.send_json({
                                "type": "conversation_switched",
                                "conversation_id": conv_id,
                            })
                        else:
                            await ws.send_json({
                                "type": "error",
                                "text": "Conversation not found or not authorized.",
                            })
                    else:
                        # Switch to no specific conversation (will use default)
                        active_conversation_id = None
                        await ws.send_json({
                            "type": "conversation_switched",
                            "conversation_id": None,
                        })
                    continue

                if msg_type == "message":
                    text = (data.get("text") or "").strip()
                    if not text:
                        continue

                    # Use authenticated user's info if available
                    if auth_user:
                        user_id = auth_user.id
                        user_name = auth_user.display_name
                    else:
                        user_id = data.get("user_id", user_id)
                        user_name = data.get("user_name", user_name)
                    _ws_clients[user_id] = ws

                    await ws.send_json({"type": "typing"})

                    try:
                        # Handle commands through the bot's command handler
                        if text.startswith("/"):
                            response = await _handle_web_command(
                                text, user_id, user_name,
                            )
                            if response is not None:
                                # Handle /admin — open dashboard
                                if response == "OPEN_ADMIN":
                                    token_val = token or ""
                                    await ws.send_json({
                                        "type": "response",
                                        "text": "",
                                        "action": "open_admin",
                                        "admin_url": f"/admin?token={token_val}",
                                    })
                                    continue
                                # Replace CONNECT_MICROSOFT with a connect action
                                if "CONNECT_MICROSOFT" in response:
                                    token_val = token or ""
                                    response = response.replace("CONNECT_MICROSOFT", "").strip()
                                    await ws.send_json({
                                        "type": "response",
                                        "text": response,
                                        "action": "connect_microsoft",
                                        "connect_url": f"/auth/microsoft?token={token_val}",
                                    })
                                    continue
                                await ws.send_json({
                                    "type": "response",
                                    "text": response,
                                })
                                continue

                        # Pre-intercept: handle action commands directly (LLM is unreliable for tool calls)
                        import re as _re
                        _handled = False

                        # -- Reminder intercept --
                        _remind_match = _re.match(
                            r"(?:remind me|set a reminder|reminder)\s+"
                            r"(in\s+\d+\s*(?:minutes?|mins?|hours?|hrs?|seconds?)"
                            r"|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
                            r"|tomorrow\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)"
                            r"\s+(?:to\s+)?(.+)",
                            text, _re.IGNORECASE,
                        )
                        if _remind_match:
                            _time_expr = _remind_match.group(1).strip()
                            _rem_msg = _remind_match.group(2).strip()
                            _due = reminder_service.parse_time_expression(_time_expr)
                            if _due:
                                reminder_service.add_reminder(user_id, _rem_msg, _due)
                                await ws.send_json({
                                    "type": "response",
                                    "text": f"Reminder set for {_due.strftime('%I:%M %p')}: {_rem_msg}",
                                })
                                _handled = True

                        # -- Email intercept (LLM drafts body, code sends) --
                        if not _handled:
                            _email_match = _re.match(
                                r"(?:send|draft|write)\s+(?:an?\s+)?(?:email|mail)\s+to\s+([\w.+-]+@[\w.-]+)"
                                r"(?:\s+with\s+subject\s+[\"']?(.+?)[\"']?)?"
                                r"\s+(?:saying|with\s+body|body|that|with\s+message|about)\s+(.+)",
                                text, _re.IGNORECASE | _re.DOTALL,
                            )
                            if _email_match:
                                _to = _email_match.group(1).strip()
                                _subject_hint = (_email_match.group(2) or "").strip()
                                _body_hint = _email_match.group(3).strip()

                                await ws.send_json({"type": "typing"})

                                # Use LLM to draft the email
                                _draft_prompt = (
                                    f"Draft a professional email.\n"
                                    f"To: {_to}\n"
                                    f"{'Subject: ' + _subject_hint if _subject_hint else 'Generate an appropriate subject.'}\n"
                                    f"The email should be about: {_body_hint}\n\n"
                                    f"Reply in this EXACT format (no other text):\n"
                                    f"SUBJECT: <subject line>\n"
                                    f"BODY:\n<email body>"
                                )
                                _draft_result = await ollama_client.chat(messages=[
                                    {"role": "system", "content": "You draft professional emails. Reply ONLY in the format requested. Sign off as Anubhav Choudhury."},
                                    {"role": "user", "content": _draft_prompt},
                                ])
                                _draft_text = _draft_result.get("message", {}).get("content", "").strip()

                                # Parse subject and body from LLM response
                                _subject = _subject_hint or "Message from MyAi"
                                _body = _body_hint
                                _subj_match = _re.search(r"SUBJECT:\s*(.+)", _draft_text)
                                _body_match = _re.search(r"BODY:\s*\n?([\s\S]+)", _draft_text)
                                if _subj_match:
                                    _subject = _subj_match.group(1).strip()
                                if _body_match:
                                    _body = _body_match.group(1).strip()

                                _result = await tool_registry._send_email(_to, _subject, _body)
                                await ws.send_json({
                                    "type": "response",
                                    "text": _result,
                                })
                                _handled = True

                        # -- WhatsApp intercept --
                        if not _handled:
                            _wa_match = _re.match(
                                r"(?:send|write)\s+(?:a\s+)?(?:whatsapp|wa)\s+(?:message\s+)?to\s+([\d+]+)\s+(?:saying|that|with\s+message)\s+(.+)",
                                text, _re.IGNORECASE | _re.DOTALL,
                            )
                            if _wa_match:
                                _phone = _wa_match.group(1).strip()
                                _wa_msg = _wa_match.group(2).strip()
                                _result = await tool_registry._send_whatsapp(_phone, _wa_msg)
                                await ws.send_json({
                                    "type": "response",
                                    "text": _result,
                                })
                                _handled = True

                        if _handled:
                            continue

                        # Process through agent with streaming support
                        # Only use streaming when NexgAI has real auth (not local mode)
                        if nexgai_client.is_configured and nexgai_client.is_available and not nexgai_client._local_mode:
                            # Use streaming path — relay NexgAI SSE chunks as WebSocket messages
                            async for event in agent.process_message_streaming(
                                user_id, text, user_name, user=auth_user,
                                conversation_id=active_conversation_id,
                            ):
                                ev_type = event.get("type")
                                if ev_type == "stream_start":
                                    await ws.send_json({
                                        "type": "stream_start",
                                        "agent": event.get("agent"),
                                        "source": event.get("source", "nexgai"),
                                    })
                                elif ev_type == "stream_chunk":
                                    await ws.send_json({
                                        "type": "stream_chunk",
                                        "text": event.get("text", ""),
                                    })
                                elif ev_type == "stream_end":
                                    await ws.send_json({
                                        "type": "stream_end",
                                        "text": event.get("text", ""),
                                        "message_id": event.get("message_id"),
                                        "conversation_id": event.get("conversation_id"),
                                        "agent": event.get("agent"),
                                        "source": event.get("source", "nexgai"),
                                    })
                                elif ev_type == "response":
                                    await ws.send_json({
                                        "type": "response",
                                        "text": event.get("text", ""),
                                        "message_id": event.get("message_id"),
                                        "conversation_id": event.get("conversation_id"),
                                        "agent": event.get("agent_name"),
                                        "source": event.get("source", "local"),
                                    })
                        else:
                            # Set reminder user context
                            tool_registry._reminder_user_id = user_id

                            # Non-streaming path (NexgAI not configured)
                            result = await agent.process_message(
                                user_id, text, user_name, user=auth_user,
                                conversation_id=active_conversation_id,
                            )
                            await ws.send_json({
                                "type": "response",
                                "text": result["text"],
                                "message_id": result["message_id"],
                                "conversation_id": result["conversation_id"],
                                "agent": result["agent_name"],
                                "source": result["source"],
                            })
                    except Exception as e:
                        logger.error(f"WebSocket message error: {e}", exc_info=True)
                        await ws.send_json({
                            "type": "error",
                            "text": f"Error: {str(e)[:300]}",
                        })

            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                break
    finally:
        # Cancel the periodic file notification task
        file_notif_task.cancel()
        try:
            await file_notif_task
        except asyncio.CancelledError:
            pass
        # Clean up
        if user_id in _ws_clients and _ws_clients[user_id] is ws:
            del _ws_clients[user_id]
        logger.info(f"WebSocket client disconnected: {user_id}")

    return ws


async def _handle_web_command(text: str, user_id: str, user_name: str) -> str | None:
    """Handle slash commands from the Web UI (reuses bot logic)."""

    # Handle /admin — return instruction to open dashboard
    if text.strip() == "/admin":
        return "OPEN_ADMIN"

    # Handle /remind command
    if text.startswith("/remind "):
        parts = text[8:].strip()
        # Parse: /remind <time> <message>
        # Examples: /remind in 5 minutes check the build
        #           /remind at 3pm sprint review
        #           /remind tomorrow at 9am standup prep
        time_keywords = ["in ", "at ", "tomorrow "]
        time_expr = ""
        message = parts

        for kw in time_keywords:
            if parts.lower().startswith(kw):
                # Find where the time expression ends and message begins
                import re
                m = re.match(
                    r"(in\s+\d+\s*(?:minutes?|seconds?|hours?|mins?|hrs?)|"
                    r"tomorrow\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?|"
                    r"at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+(.*)",
                    parts, re.IGNORECASE,
                )
                if m:
                    time_expr = m.group(1).strip()
                    message = m.group(2).strip()
                break

        if not time_expr:
            return "**Usage:** `/remind <time> <message>`\n\nExamples:\n- `/remind in 5 minutes check the build`\n- `/remind at 3pm sprint review`\n- `/remind tomorrow at 9am prepare standup`"

        due_at = reminder_service.parse_time_expression(time_expr)
        if not due_at:
            return f"Couldn't understand the time: '{time_expr}'. Try 'in 5 minutes', 'at 3pm', or 'tomorrow at 9am'."

        if not message:
            return "Please include a message for the reminder."

        reminder = reminder_service.add_reminder(user_id, message, due_at)
        return f"**Reminder set!**\n\n{message}\n\nDue: {due_at.strftime('%I:%M %p, %B %d')}"

    # Handle /reminders command — list active reminders
    if text.strip() == "/reminders":
        reminders = reminder_service.list_reminders(user_id)
        if not reminders:
            return "No active reminders."
        lines = ["**Active Reminders:**\n"]
        for r in reminders:
            lines.append(f"- {r.message} — {r.due_at.strftime('%I:%M %p, %B %d')} (`{r.id}`)")
        return "\n".join(lines)

    # Reuse the bot's command handler with a dummy say function
    responses = []

    async def say(text, **kwargs):
        responses.append(text)

    result = await bot._handle_command(text, user_id, user_name, "web", say)
    if result is not None:
        return result
    if responses:
        return "\n".join(responses)
    return None


# -- WhatsApp Webhook --

async def whatsapp_webhook(req: web.Request) -> web.Response:
    """Handle incoming WhatsApp messages from Twilio."""
    try:
        data = await req.post()
        from_number = data.get("From", "").replace("whatsapp:", "")
        body = data.get("Body", "").strip()

        if not body:
            return web.Response(text="", content_type="text/xml")

        logger.info(f"WhatsApp message from {from_number}: {body}")

        # Find the first connected web user to link WhatsApp to their account
        # This way WhatsApp messages appear in the same account's conversations
        linked_user_id = None
        linked_user_name = "User"
        for client_id in _ws_clients:
            if not client_id.startswith("wa-"):
                linked_user_id = client_id
                break

        # Create or get a WhatsApp conversation for this user
        wa_conv_title = f"WhatsApp ({from_number[-4:]})"
        if linked_user_id:
            user_id_for_msg = linked_user_id
            # Find existing WhatsApp conversation or create one
            convos = await database.list_conversations(linked_user_id)
            wa_conv_id = None
            for c in convos:
                if c.get("title", "").startswith("WhatsApp"):
                    wa_conv_id = c["id"]
                    break
            if not wa_conv_id:
                wa_conv_id = await database.create_conversation(linked_user_id, wa_conv_title)
        else:
            user_id_for_msg = f"wa-{from_number}"
            wa_conv_id = None

        # Pre-intercept reminders (LLM is unreliable)
        import re as _re
        _remind_match = _re.match(
            r"(?:remind me|set a reminder|reminder)\s+"
            r"(in\s+\d+\s*(?:minutes?|mins?|hours?|hrs?|seconds?)"
            r"|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
            r"|tomorrow\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)"
            r"\s+(?:to\s+)?(.+)",
            body, _re.IGNORECASE,
        )
        if _remind_match:
            _time_expr = _remind_match.group(1).strip()
            _rem_msg = _remind_match.group(2).strip()
            _due = reminder_service.parse_time_expression(_time_expr)
            if _due:
                reminder_service.add_reminder(user_id_for_msg, _rem_msg, _due)
                twiml = whatsapp_service.create_twiml_response(
                    f"Reminder set for {_due.strftime('%I:%M %p')}: {_rem_msg}"
                )
                return web.Response(text=twiml, content_type="text/xml")

        # Process through the agent using the linked user's account
        result = await agent.process_message(
            user_id_for_msg, body, user_name=linked_user_name,
            conversation_id=wa_conv_id,
        )
        response_text = result.get("text", "Sorry, something went wrong.")

        # Truncate if too long for WhatsApp (1600 char limit)
        if len(response_text) > 1500:
            response_text = response_text[:1500] + "..."

        # Silently refresh conversation list in web UI (no notifications)
        for client_id, client_ws in _ws_clients.items():
            if not client_ws.closed:
                try:
                    await client_ws.send_json({"type": "conversations_updated"})
                except Exception:
                    pass

        # Send reply via TwiML
        twiml = whatsapp_service.create_twiml_response(response_text)
        return web.Response(text=twiml, content_type="text/xml")

    except Exception as e:
        logger.error(f"WhatsApp webhook error: {e}", exc_info=True)
        twiml = whatsapp_service.create_twiml_response("Sorry, an error occurred.")
        return web.Response(text=twiml, content_type="text/xml")


# -- Microsoft Connect redirect --

async def microsoft_connect(req: web.Request) -> web.Response:
    """Redirect to Microsoft OAuth2 login page."""
    token = req.query.get("token", "")
    if not token:
        return web.json_response({"error": "No token"}, status=401)
    user = await auth_service.validate_session(token)
    if not user:
        return web.json_response({"error": "Invalid token"}, status=401)
    if not graph_client.is_configured:
        return web.Response(text="Microsoft 365 not configured", status=500)
    auth_url = graph_client.get_auth_url(state=user.id)
    raise web.HTTPFound(location=auth_url)


# -- Web UI API endpoints --

async def chat_history(req: web.Request) -> web.Response:
    """Return chat history for the authenticated user."""
    try:
        token = _extract_token(req)
        if not token:
            return web.json_response({"error": "No token provided"}, status=401)

        user = await auth_service.validate_session(token)
        if not user:
            return web.json_response({"error": "Invalid or expired token"}, status=401)

        limit = int(req.query.get("limit", "50"))
        limit = max(1, min(limit, 200))  # clamp between 1 and 200

        messages = await database.get_chat_history(user.id, limit=limit)
        return web.json_response({"messages": messages})
    except Exception as e:
        logger.error(f"Chat history error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def list_conversations(req: web.Request) -> web.Response:
    """List all conversations for the authenticated user."""
    try:
        token = _extract_token(req)
        if not token:
            return web.json_response({"error": "No token provided"}, status=401)
        user = await auth_service.validate_session(token)
        if not user:
            return web.json_response({"error": "Invalid or expired token"}, status=401)

        conversations = await database.list_conversations(user.id)
        return web.json_response({"conversations": conversations})
    except Exception as e:
        logger.error(f"List conversations error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def create_conversation(req: web.Request) -> web.Response:
    """Create a new conversation for the authenticated user."""
    try:
        token = _extract_token(req)
        if not token:
            return web.json_response({"error": "No token provided"}, status=401)
        user = await auth_service.validate_session(token)
        if not user:
            return web.json_response({"error": "Invalid or expired token"}, status=401)

        title = ""
        try:
            body = await req.json()
            title = (body.get("title") or "").strip()
        except Exception:
            pass

        conv_id = await database.create_conversation(user.id, title=title)
        return web.json_response({"conversation_id": conv_id, "title": title or "New Chat"})
    except Exception as e:
        logger.error(f"Create conversation error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_conversation_endpoint(req: web.Request) -> web.Response:
    """Delete a conversation owned by the authenticated user."""
    try:
        token = _extract_token(req)
        if not token:
            return web.json_response({"error": "No token provided"}, status=401)
        user = await auth_service.validate_session(token)
        if not user:
            return web.json_response({"error": "Invalid or expired token"}, status=401)

        conv_id = req.match_info["id"]
        # Verify ownership
        owner = await database.get_conversation_owner(conv_id)
        if owner != user.id:
            return web.json_response({"error": "Not found or not authorized"}, status=404)

        await database.delete_conversation(conv_id)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"Delete conversation error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def rename_conversation_endpoint(req: web.Request) -> web.Response:
    """Rename a conversation owned by the authenticated user."""
    try:
        token = _extract_token(req)
        if not token:
            return web.json_response({"error": "No token provided"}, status=401)
        user = await auth_service.validate_session(token)
        if not user:
            return web.json_response({"error": "Invalid or expired token"}, status=401)

        conv_id = req.match_info["id"]
        owner = await database.get_conversation_owner(conv_id)
        if owner != user.id:
            return web.json_response({"error": "Not found or not authorized"}, status=404)

        body = await req.json()
        title = (body.get("title") or "").strip()
        if not title:
            return web.json_response({"error": "title is required"}, status=400)

        await database.rename_conversation(conv_id, title)
        return web.json_response({"status": "ok", "title": title})
    except Exception as e:
        logger.error(f"Rename conversation error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def conversation_history(req: web.Request) -> web.Response:
    """Return chat history for a specific conversation."""
    try:
        token = _extract_token(req)
        if not token:
            return web.json_response({"error": "No token provided"}, status=401)
        user = await auth_service.validate_session(token)
        if not user:
            return web.json_response({"error": "Invalid or expired token"}, status=401)

        conv_id = req.match_info["id"]
        owner = await database.get_conversation_owner(conv_id)
        if owner != user.id:
            return web.json_response({"error": "Not found or not authorized"}, status=404)

        limit = int(req.query.get("limit", "50"))
        limit = max(1, min(limit, 200))

        messages = await database.get_chat_history(user.id, limit=limit, conversation_id=conv_id)
        return web.json_response({"messages": messages})
    except Exception as e:
        logger.error(f"Conversation history error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def web_status(req: web.Request) -> web.Response:
    """Status info for the Web UI sidebar."""
    ollama_ok = await ollama_client.health_check()
    graph_status = False
    if graph_client.is_configured:
        graph_status = "configured"

    nexgai_status = False
    if nexgai_client.is_configured:
        nexgai_status = "configured"
        if nexgai_client.circuit_breaker.is_open:
            nexgai_status = "circuit_open"

    return web.json_response({
        "ollama": ollama_ok,
        "model": ollama_client.model,
        "graph": graph_status,
        "nexgai": nexgai_status,
        "search": search_service.enabled if search_service else False,
    })


async def web_skills(req: web.Request) -> web.Response:
    """List available agents for the Web UI sidebar."""
    skills = []

    # NexgAI platform agents
    if nexgai_client.is_configured and nexgai_client.is_available:
        try:
            nexgai_agents = await nexgai_client.list_agents()
            for a in nexgai_agents:
                skills.append({
                    "name": a.name,
                    "agent": a.display_name,
                    "description": a.description,
                    "source": "nexgai",
                    "agent_type": a.agent_type,
                })
        except Exception as e:
            logger.warning(f"Failed to fetch NexgAI agents for skills list: {e}")

    # Always include the general-purpose LLM
    skills.append({
        "name": "general",
        "agent": "MyAi",
        "description": "General-purpose assistant powered by Ollama LLM",
        "source": "local",
    })

    return web.json_response({"skills": skills})


async def web_index(req: web.Request) -> web.FileResponse:
    """Serve the Web UI index page."""
    return web.FileResponse(Path(__file__).parent.parent / "web" / "index.html")


def create_debug_app() -> web.Application:
    """Create the HTTP server for debug endpoints, Web UI, admin dashboard, and WebSocket."""
    app = web.Application()

    # Inject services into app dict for admin routes
    analytics_service = AnalyticsService(database)
    app["analytics_service"] = analytics_service
    app["auth_service"] = auth_service
    app["rbac_service"] = rbac_service
    app["database"] = database
    app["indexing_service"] = indexing_service
    app["encryption"] = encryption
    app["rag_service"] = rag_service
    app["nexgai_client"] = nexgai_client
    app["feedback_service"] = feedback_service
    app["learning_engine"] = learning_engine
    app["agent_core"] = agent

    # Health & debug
    app.router.add_get("/health", health)
    app.router.add_get("/auth/callback", auth_callback)
    app.router.add_get("/auth/microsoft", microsoft_connect)
    app.router.add_post("/whatsapp/webhook", whatsapp_webhook)
    app.router.add_post("/api/simulate-transcript", simulate_transcript)
    app.router.add_get("/api/debug/sessions", debug_sessions)

    # Auth API
    app.router.add_get("/api/auth/setup-status", auth_setup_status)
    app.router.add_post("/api/auth/setup", auth_setup)
    app.router.add_post("/api/auth/login", auth_login)
    app.router.add_post("/api/auth/logout", auth_logout)
    app.router.add_get("/api/auth/me", auth_me)

    # Web UI API
    app.router.add_get("/api/web/status", web_status)
    app.router.add_get("/api/web/skills", web_skills)

    # Chat history API
    app.router.add_get("/api/chat/history", chat_history)

    # Conversations API (multi-conversation support)
    app.router.add_get("/api/conversations", list_conversations)
    app.router.add_post("/api/conversations", create_conversation)
    app.router.add_delete("/api/conversations/{id}", delete_conversation_endpoint)
    app.router.add_post("/api/conversations/{id}/rename", rename_conversation_endpoint)
    app.router.add_get("/api/conversations/{id}/history", conversation_history)

    # Admin dashboard routes
    setup_admin_routes(app)
    setup_datasource_routes(app)
    setup_learning_routes(app)

    # WebSocket
    app.router.add_get("/ws", websocket_handler)

    # Static files (CSS, JS)
    static_dir = Path(__file__).parent.parent / "web"
    if static_dir.exists():
        app.router.add_static("/static", static_dir, show_index=False)

    # Web UI (serve index.html at root)
    app.router.add_get("/", web_index)

    return app


async def _session_cleanup_loop():
    """Periodically clean up expired sessions."""
    while True:
        try:
            removed = await auth_service.cleanup_expired_sessions()
            if removed > 0:
                logger.info(f"Cleaned up {removed} expired session(s)")
        except Exception as e:
            logger.warning(f"Session cleanup error: {e}")
        await asyncio.sleep(3600)  # Run every hour


async def _daily_briefing_loop():
    """Send daily briefing at 10 AM via WhatsApp."""
    from datetime import datetime, timedelta
    from app.services.briefing import generate_briefing

    while True:
        try:
            now = datetime.now()
            # Calculate seconds until next 10 AM
            target = now.replace(hour=10, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            logger.info(f"Daily WhatsApp briefing scheduled in {wait_seconds/3600:.1f} hours")
            await asyncio.sleep(wait_seconds)

            # Generate briefing
            briefing = await generate_briefing(
                user_name="Anubhav",
                user_id="daily-briefing",
                ollama=ollama_client,
                database=database,
            )
            if briefing and whatsapp_service.is_configured:
                # Send to user's WhatsApp
                user_phone = "+917205638079"
                await whatsapp_service.send_message(user_phone, f"🌅 *MyAi Daily Briefing*\n\n{briefing}")
                logger.info("Daily briefing sent via WhatsApp")

                # Also push to web UI if connected
                for client_id, client_ws in _ws_clients.items():
                    if not client_ws.closed:
                        try:
                            await client_ws.send_json({
                                "type": "response",
                                "text": briefing,
                                "source": "briefing",
                            })
                        except Exception:
                            pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Daily briefing error: {e}")
            await asyncio.sleep(3600)  # Retry in 1 hour


async def _learning_engine_loop():
    """Periodically run the learning engine to analyze feedback."""
    interval = settings.learning_interval_hours * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            summary = await learning_engine.run_cycle()
            total = sum(summary.values())
            if total:
                logger.info("Learning cycle generated %d entries: %s", total, summary)
        except Exception as e:
            logger.warning("Learning engine error: %s", e)


async def on_startup(web_only: bool = False):
    """Initialize database on startup."""
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)
    await database.init()

    # Load any admin-approved system prompt from the database
    active_prompt = await database.get_active_prompt("local")
    if active_prompt:
        agent._prompt_override = active_prompt
        logger.info("Loaded active prompt version from database")

    # Authenticate with NexgAI if configured
    nexgai_status = "Not configured"
    if nexgai_client.is_configured:
        if await nexgai_client.authenticate():
            nexgai_status = "Connected"
        else:
            nexgai_status = "Auth failed (will retry on first request)"

    mode = "Web Only" if web_only else "Slack + Web"
    logger.info("=" * 60)
    logger.info(f"MyAi Agent Started ({mode})")
    logger.info(f"   Model:    {ollama_client.model}")
    logger.info(f"   Web UI:   http://{settings.host}:{settings.port}")
    logger.info(f"   Graph:    {'Configured' if graph_client.is_configured else 'Not configured'}")
    logger.info(f"   NexgAI:   {nexgai_status}")
    logger.info("=" * 60)


async def run_async(web_only: bool = False):
    """Run the HTTP/WebSocket server, and optionally Slack Socket Mode."""
    global _slack_app

    await on_startup(web_only=web_only)

    # Start background tasks
    asyncio.create_task(_session_cleanup_loop())
    asyncio.create_task(_learning_engine_loop())
    asyncio.create_task(_daily_briefing_loop())

    # Start reminder service
    async def _reminder_notify(user_id: str, message: str):
        """Send reminder notification via WebSocket + WhatsApp."""
        logger.info(f"Reminder firing for user {user_id}: {message}")

        # Send via WebSocket to ALL connected clients
        for client_id, client_ws in _ws_clients.items():
            if not client_ws.closed:
                try:
                    await client_ws.send_json({"type": "system", "text": message, "source": "reminder"})
                    logger.info(f"Reminder sent to WebSocket for {client_id}")
                except Exception as e:
                    logger.error(f"Failed to send reminder via WebSocket: {e}")

        # Always send via WhatsApp if configured (so user gets phone notification)
        if whatsapp_service.is_configured:
            clean_msg = message.replace("**", "")
            user_phone = "+917205638079"
            try:
                await whatsapp_service.send_message(user_phone, f"🔔 {clean_msg}")
                logger.info(f"Reminder sent via WhatsApp to {user_phone}")
            except Exception as e:
                logger.warning(f"WhatsApp reminder failed: {e}")

    reminder_service.set_notify_callback(_reminder_notify)
    asyncio.create_task(reminder_service.check_loop())

    # Start file watcher
    try:
        file_watcher.start()
    except Exception as e:
        logger.warning(f"File watcher failed to start: {e}")

    # Start HTTP server (Web UI + debug + WebSocket)
    debug_app = create_debug_app()
    runner = web.AppRunner(debug_app)
    await runner.setup()
    site = web.TCPSite(runner, settings.host, settings.port)
    await site.start()
    logger.info(f"HTTP server running on http://{settings.host}:{settings.port}")
    logger.info(f"Web UI available at http://localhost:{settings.port}")

    if web_only:
        logger.info("Running in web-only mode (no Slack connection)")
        # Keep the server running
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        return

    # Create and configure Slack app
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    slack_app = AsyncApp(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
    )
    _slack_app = slack_app

    # Register event handlers
    @slack_app.event("message")
    async def on_message(body, say, client):
        await bot.handle_message(body, say, client)

    @slack_app.event("app_mention")
    async def on_app_mention(body, say, client):
        await bot.handle_app_mention(body, say, client)

    @slack_app.command("/myai")
    async def on_slash_myai(ack, body, say, client):
        await bot.handle_slash_command(ack, body, say, client)

    # Start Slack Socket Mode handler
    handler = AsyncSocketModeHandler(slack_app, settings.slack_app_token)
    logger.info("Connecting to Slack via Socket Mode...")
    await handler.start_async()


def main():
    web_only = "--web-only" in sys.argv
    asyncio.run(run_async(web_only=web_only))


if __name__ == "__main__":
    main()
