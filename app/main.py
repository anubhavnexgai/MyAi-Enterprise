"""MyAi — Local AI Agent for Microsoft Teams.

Run with: python -m app.main
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
import httpx

from aiohttp import web
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
)
from botbuilder.schema import Activity

from app.config import settings
from app.agent.core import AgentCore
from app.agent.tools import ToolRegistry
from app.bot import MyAiBot
from app.services.ollama import OllamaClient
from app.services.file_access import FileAccessService
from app.services.web_search import WebSearchService
from app.services.rag import RAGService
from app.services.graph import GraphClient
from app.services.meeting_transcript import MeetingTranscriptService
from app.storage.database import Database

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("miai")

# ── Initialize services ──
ollama_client = OllamaClient()
file_service = FileAccessService()
search_service = WebSearchService()
rag_service = RAGService(ollama_client)
graph_client = GraphClient()
database = Database(settings.database_path)


async def _deliver_suggestion(session, suggestion: str) -> None:
    """Deliver a meeting suggestion to the user via proactive Teams message."""
    ref = session.conversation_reference
    service_url = ref.get("service_url")
    conversation_id = ref.get("conversation_id")
    if not service_url or not conversation_id:
        logger.warning("Cannot deliver suggestion: missing conversation reference")
        return
    formatted = (
        f"**Meeting Suggestion**\n\n"
        f"_{suggestion}_"
    )
    await graph_client.send_proactive_message(service_url, conversation_id, formatted)


# ── Initialize meeting transcript service ──
meeting_service = MeetingTranscriptService(
    ollama=ollama_client,
    deliver_fn=_deliver_suggestion,
    graph_client=graph_client,
    database=database,
)

# ── Initialize agent ──
tool_registry = ToolRegistry(file_service, search_service, rag_service)
agent = AgentCore(ollama_client, tool_registry, database)

# ── Initialize bot ──
adapter_settings = BotFrameworkAdapterSettings(
    app_id=settings.microsoft_app_id,
    app_password=settings.microsoft_app_password,
    channel_auth_tenant=settings.microsoft_app_tenant_id or None,
)
adapter = BotFrameworkAdapter(adapter_settings)
bot = MyAiBot(agent, search_service, graph_client, meeting_service, database)


# ── Error handler ──
async def on_error(context, error):
    logger.error(f"Bot error: {error}", exc_info=True)
    await context.send_activity("⚠️ An internal error occurred. Please try again.")


adapter.on_turn_error = on_error


# ── aiohttp web app (required by botbuilder) ──
async def messages(req: web.Request) -> web.Response:
    """Main webhook endpoint for Teams Bot Framework."""
    if "application/json" not in req.headers.get("Content-Type", ""):
        return web.Response(status=415)

    body = await req.json()
    activity = Activity().deserialize(body)

    auth_header = req.headers.get("Authorization", "")

    response = await adapter.process_activity(activity, auth_header, bot.on_turn)

    if response:
        return web.json_response(data=response.body, status=response.status)
    return web.Response(status=201)


async def health(req: web.Request) -> web.Response:
    """Health check endpoint."""
    ollama_ok = await ollama_client.health_check()
    return web.json_response({
        "status": "ok" if ollama_ok else "degraded",
        "ollama": "connected" if ollama_ok else "unreachable",
        "model": ollama_client.model,
    })


async def calling(req: web.Request) -> web.Response:
    """Webhook endpoint for Teams Calling events."""
    if "application/json" not in req.headers.get("Content-Type", ""):
        return web.Response(status=415)

    try:
        body = await req.json()
        logger.info("Incoming calling event block received.")

        token = await graph_client.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        for event in body.get("value", [body]):
            resource_data = event.get("resourceData", {})
            # resourceData can sometimes be a list — skip those
            if isinstance(resource_data, list):
                logger.warning(f"resourceData is a list, skipping: {resource_data}")
                continue
            state = resource_data.get("state")
            # call_id can be in resourceData.id, or parsed from the resource URL
            call_id = resource_data.get("id")
            if not call_id:
                # Try to extract from resource URL: /communications/calls/{id}
                resource_url = event.get("resource", "")
                import re
                m = re.search(r'/communications/calls/([^/]+)', resource_url)
                if m:
                    call_id = m.group(1)
            logger.info(f"Calling event: state={state}, call_id={call_id}, changeType={event.get('changeType')}")

            # 1. Answer an Incoming Call
            if event.get("changeType") == "created" and state == "incoming":
                logger.info(f"Ringing... Bot was invited to call {call_id}!")

                call_route = f"https://graph.microsoft.com/v1.0/communications/calls/{call_id}/answer"
                cb_host = settings.callback_host or f"https://{req.host}"
                callback_url = f"{cb_host.rstrip('/')}/api/calling"

                payload = {
                    "callbackUri": callback_url,
                    "acceptedModalities": ["audio"],
                    "mediaConfig": {
                        "@odata.type": "#microsoft.graph.serviceHostedMediaConfig"
                    },
                }

                async with httpx.AsyncClient() as client:
                    resp = await client.post(call_route, headers=headers, json=payload)
                    logger.info(f"Answered call: {resp.status_code}")

            # 2. Subscribe to Transcripts once the call is Established
            elif state == "established":
                logger.info(f"Call {call_id} is now established! Subscribing to transcript.")

                meeting_info = resource_data.get("meetingInfo", {})
                meeting_id = None
                join_url = meeting_info.get("joinWebUrl")

                if join_url:
                    meeting_id = await graph_client.resolve_meeting_id_from_join_url(join_url)
                elif meeting_info.get("@odata.type") == "#microsoft.graph.organizerMeetingInfo":
                    meeting_id = meeting_info.get("organizer", {}).get("user", {}).get("id")

                # Start a meeting session if we don't have one yet
                if not meeting_service.get_session(call_id):
                    pending = getattr(bot, "_pending_join_context", {})
                    session_info = pending.get(call_id) or pending.get("_latest", {})
                    meeting_service.start_session(
                        call_id=call_id,
                        user_id=session_info.get("user_id", "unknown"),
                        user_name=session_info.get("user_name", "User"),
                        meeting_subject=session_info.get("meeting_subject", ""),
                        meeting_id=meeting_id or "",
                        conversation_reference=session_info.get("conversation_reference", {}),
                    )
                else:
                    # Update meeting_id if we resolved it now
                    existing = meeting_service.get_session(call_id)
                    if meeting_id and existing and not existing.meeting_id:
                        existing.meeting_id = meeting_id

                # Use CALLBACK_HOST for the transcript webhook URL
                callback_host = settings.callback_host
                if not callback_host:
                    callback_host = f"https://{req.host}"
                transcript_url = f"{callback_host.rstrip('/')}/api/transcript-webhook"

                if meeting_id:
                    try:
                        await graph_client.subscribe_to_transcript(meeting_id, transcript_url)
                        logger.info(f"Subscribed to transcript for meeting {meeting_id}")
                    except Exception as e:
                        logger.error(f"Transcript subscription failed: {e}", exc_info=True)
                        # Polling fallback will still work if meeting_id is set
                else:
                    logger.warning(
                        f"Could not resolve meeting ID for call {call_id}. "
                        "Polling fallback will not work without a meeting ID."
                    )

            # 3. Handle call termination
            elif state == "terminated":
                logger.info(f"Call {call_id} terminated.")
                meeting_service.end_session(call_id)

        return web.json_response({"status": "acknowledged"})
    except Exception as e:
        logger.error(f"Calling webhook error: {e}", exc_info=True)
        return web.Response(status=500)


async def transcript_webhook(req: web.Request) -> web.Response:
    """Webhook endpoint for Microsoft Graph meeting transcript subscriptions."""
    validation_token = req.query.get("validationToken")
    if validation_token:
        logger.info("Validating Microsoft Graph transcript webhook...")
        return web.Response(text=validation_token, content_type="text/plain")

    if "application/json" not in req.headers.get("Content-Type", ""):
        return web.Response(status=415)

    try:
        body = await req.json()

        for notification in body.get("value", []):
            # Validate clientState to reject spoofed notifications
            client_state = notification.get("clientState", "")
            if client_state != settings.transcript_webhook_secret:
                logger.warning(f"Invalid clientState in transcript notification, ignoring")
                continue

            resource_url = notification.get("resource")
            if not resource_url:
                continue

            logger.info(f"New transcript block available at: {resource_url}")

            # Fetch the actual transcript text
            token = await graph_client.get_access_token()
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://graph.microsoft.com/v1.0/{resource_url}?$format=text/vtt",
                    headers={"Authorization": f"Bearer {token}"},
                )

                if resp.status_code != 200:
                    logger.warning(f"Failed to fetch transcript: {resp.status_code}")
                    continue

                transcript_text = resp.text
                logger.info(f"[LIVE TRANSCRIPT] {len(transcript_text)} chars received")

            # Route to the correct meeting session.
            # The resource URL contains the meeting ID; try to match by
            # finding any active session (for single-meeting scenarios)
            # or by extracting the meeting/call ID from the resource path.
            call_id = _resolve_call_id_from_resource(resource_url)
            if call_id:
                await meeting_service.ingest_transcript(call_id, transcript_text)
            else:
                # Fallback: feed to all active sessions
                for sid in meeting_service.active_sessions:
                    await meeting_service.ingest_transcript(sid, transcript_text)

        return web.Response(status=202)
    except Exception as e:
        logger.error(f"Transcript webhook error: {e}", exc_info=True)
        return web.Response(status=500)


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
            # Parse and append transcript lines directly (skip debounce)
            new_lines = meeting_service._parse_transcript_text(transcript_text)
            if new_lines:
                session.transcript_lines.extend(new_lines)

            # Force generate a suggestion immediately (no debounce)
            # Reset hash/time so it doesn't skip
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
            "meeting_id": s.meeting_id,
            "conversation_reference": s.conversation_reference,
            "transcript_line_count": len(s.transcript_lines),
            "last_suggestion": s.last_suggestion[:200] if s.last_suggestion else None,
        })
    return web.json_response({"active_sessions": data})


def _resolve_call_id_from_resource(resource_url: str) -> str | None:
    """Try to extract a call ID from a Graph resource URL and match to an active session."""
    # Resource URLs look like: communications/onlineMeetings/{id}/transcripts/{id}
    # We may not have a direct call_id mapping, so try active sessions
    sessions = meeting_service.active_sessions
    if len(sessions) == 1:
        return next(iter(sessions))
    return None


async def on_startup(app: web.Application):
    """Initialize database on startup."""
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)
    await database.init()
    logger.info("=" * 60)
    logger.info("🐾  MyAi Agent Started")
    logger.info(f"   Model:    {ollama_client.model}")
    logger.info(f"   Server:   http://{settings.host}:{settings.port}")
    logger.info(f"   Webhook:  http://{settings.host}:{settings.port}/api/messages")
    logger.info(f"   Health:   http://{settings.host}:{settings.port}/health")
    logger.info("=" * 60)
    logger.info("Waiting for Teams messages...")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/health", health)
    app.router.add_post("/api/calling", calling)
    app.router.add_post("/api/transcript-webhook", transcript_webhook)
    app.router.add_post("/api/simulate-transcript", simulate_transcript)
    app.router.add_get("/api/debug/sessions", debug_sessions)
    app.on_startup.append(on_startup)
    return app


def main():
    app = create_app()
    web.run_app(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
