"""MyAi -- Local AI Agent for Slack + Web UI.

Run with: python -m app.main           (Slack + Web UI)
          python -m app.main --web-only (Web UI only, no Slack)
"""
from __future__ import annotations

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
from app.learning.feedback_service import FeedbackService
from app.learning.engine import LearningEngine
from app.learning.routes import setup_learning_routes
from app.storage.database import Database

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

# -- Initialize agent (NexgAI agents + Ollama LLM) --
agent = AgentCore(
    ollama_client, database,
    nexgai_client=nexgai_client if nexgai_client.is_configured else None,
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

bot = SlackBot(agent, None, meeting_service, database, graph_client=graph_client)


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

    logger.info(f"WebSocket client connected from {req.remote}")

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
                                await ws.send_json({
                                    "type": "response",
                                    "text": response,
                                })
                                continue

                        # Process through agent with streaming support
                        if nexgai_client.is_configured and nexgai_client.is_available:
                            # Use streaming path — relay NexgAI SSE chunks as WebSocket messages
                            async for event in agent.process_message_streaming(
                                user_id, text, user_name, user=auth_user,
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
                            # Non-streaming path (NexgAI not configured)
                            result = await agent.process_message(
                                user_id, text, user_name, user=auth_user,
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
        # Clean up
        if user_id in _ws_clients and _ws_clients[user_id] is ws:
            del _ws_clients[user_id]
        logger.info(f"WebSocket client disconnected: {user_id}")

    return ws


async def _handle_web_command(text: str, user_id: str, user_name: str) -> str | None:
    """Handle slash commands from the Web UI (reuses bot logic)."""
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


# -- Web UI API endpoints --

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
