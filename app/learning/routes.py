"""HTTP routes for the self-learning loop: feedback API + admin learning dashboard."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from aiohttp import web

logger = logging.getLogger("miai.learning.routes")


async def _get_authenticated_user(req: web.Request):
    """Validate Bearer token and return the user, or None."""
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    auth = req.app.get("auth_service")
    if not auth:
        return None
    return await auth.validate_session(token)


async def _get_authenticated_admin(req: web.Request):
    """Validate Bearer token and check admin role. Returns (user, error_response)."""
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, web.json_response({"error": "Missing Authorization header"}, status=401)

    token = auth_header[7:]
    auth = req.app.get("auth_service")
    rbac = req.app.get("rbac_service")
    if not auth or not rbac:
        return None, web.json_response({"error": "Auth services not configured"}, status=503)

    user = await auth.validate_session(token)
    if not user:
        return None, web.json_response({"error": "Invalid or expired token"}, status=401)

    if not rbac.is_admin(user):
        return None, web.json_response({"error": "Admin access required"}, status=403)

    return user, None


# ── Feedback Endpoints (authenticated users) ──


async def submit_feedback(req: web.Request) -> web.Response:
    """POST /api/feedback — submit thumbs up/down on a message."""
    user = await _get_authenticated_user(req)
    if not user:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    message_id = body.get("message_id")
    conversation_id = body.get("conversation_id", "")
    rating = body.get("rating")
    comment = body.get("comment", "")
    source = body.get("source", "local")
    agent_name = body.get("agent_name")

    if not message_id or rating not in ("up", "down"):
        return web.json_response({"error": "message_id and rating (up/down) required"}, status=400)

    feedback_svc = req.app.get("feedback_service")
    if not feedback_svc:
        return web.json_response({"error": "Feedback service not available"}, status=503)

    feedback_id = await feedback_svc.submit(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user.id,
        rating=rating,
        comment=comment,
        source=source,
        agent_name=agent_name,
    )

    return web.json_response({"status": "ok", "feedback_id": feedback_id})


async def feedback_stats(req: web.Request) -> web.Response:
    """GET /api/feedback/stats — satisfaction stats."""
    user = await _get_authenticated_user(req)
    if not user:
        return web.json_response({"error": "Authentication required"}, status=401)

    period = int(req.query.get("period_hours", "24"))
    source = req.query.get("source")

    feedback_svc = req.app.get("feedback_service")
    if not feedback_svc:
        return web.json_response({"error": "Feedback service not available"}, status=503)

    stats = await feedback_svc.get_stats(period, source)
    return web.json_response(stats)


# ── Learning Dashboard Endpoints (admin only) ──


async def learning_pending(req: web.Request) -> web.Response:
    """GET /api/admin/learning/pending — list pending learning entries."""
    user, err = await _get_authenticated_admin(req)
    if err:
        return err

    db = req.app["database"]
    entries = await db.get_learning_entries(status="pending", limit=50)
    return web.json_response({"entries": entries})


async def learning_history(req: web.Request) -> web.Response:
    """GET /api/admin/learning/history — list all learning entries."""
    user, err = await _get_authenticated_admin(req)
    if err:
        return err

    db = req.app["database"]
    status = req.query.get("status")
    entry_type = req.query.get("type")
    limit = int(req.query.get("limit", "50"))
    offset = int(req.query.get("offset", "0"))

    entries = await db.get_learning_entries(status=status, entry_type=entry_type, limit=limit, offset=offset)
    return web.json_response({"entries": entries})


async def learning_approve(req: web.Request) -> web.Response:
    """POST /api/admin/learning/{entry_id}/approve — approve a learning entry."""
    user, err = await _get_authenticated_admin(req)
    if err:
        return err

    entry_id = req.match_info["entry_id"]
    db = req.app["database"]

    from datetime import datetime
    updated = await db.update_learning_entry(
        entry_id,
        status="approved",
        reviewed_by=user.id,
        reviewed_at=datetime.utcnow().isoformat(),
    )
    if not updated:
        return web.json_response({"error": "Entry not found"}, status=404)

    # Apply side effects based on entry type
    entries = await db.get_learning_entries(status="approved", limit=1)
    entry = next((e for e in entries if e["id"] == entry_id), None)

    if entry and entry["entry_type"] == "prompt_refinement":
        # Create new active prompt version
        current_prompt = await db.get_active_prompt("local")
        suggested = entry["suggested_improvement"]

        # Build the new prompt: append the refinement to the current prompt
        from app.agent.prompts import SYSTEM_PROMPT
        base = current_prompt or SYSTEM_PROMPT
        new_prompt = base.rstrip() + "\n\n## Learned Refinement\n" + suggested

        await db.add_prompt_version({
            "id": str(uuid.uuid4()),
            "source": "local",
            "prompt_text": new_prompt,
            "learning_entry_id": entry_id,
            "created_by": user.id,
        })

        # Notify AgentCore to reload prompt
        agent_core = req.app.get("agent_core")
        if agent_core and hasattr(agent_core, "_prompt_override"):
            agent_core._prompt_override = new_prompt

        logger.info("Prompt refinement applied: entry %s", entry_id)

    return web.json_response({"status": "approved", "entry_id": entry_id})


async def learning_reject(req: web.Request) -> web.Response:
    """POST /api/admin/learning/{entry_id}/reject — reject a learning entry."""
    user, err = await _get_authenticated_admin(req)
    if err:
        return err

    entry_id = req.match_info["entry_id"]
    db = req.app["database"]

    from datetime import datetime
    updated = await db.update_learning_entry(
        entry_id,
        status="rejected",
        reviewed_by=user.id,
        reviewed_at=datetime.utcnow().isoformat(),
    )
    if not updated:
        return web.json_response({"error": "Entry not found"}, status=404)

    return web.json_response({"status": "rejected", "entry_id": entry_id})


async def satisfaction_trend(req: web.Request) -> web.Response:
    """GET /api/admin/learning/satisfaction-trend — satisfaction data for chart."""
    user, err = await _get_authenticated_admin(req)
    if err:
        return err

    days = int(req.query.get("days", "30"))
    db = req.app["database"]
    data = await db.get_satisfaction_trend(days)
    return web.json_response({"trend": data})


async def prompt_versions(req: web.Request) -> web.Response:
    """GET /api/admin/learning/prompt-versions — list prompt version history."""
    user, err = await _get_authenticated_admin(req)
    if err:
        return err

    db = req.app["database"]
    versions = await db.get_prompt_versions("local")
    return web.json_response({"versions": versions})


async def learning_page(req: web.Request) -> web.Response:
    """Serve the learning dashboard HTML page."""
    html_path = Path(__file__).parent.parent.parent / "web" / "learning.html"
    if html_path.exists():
        return web.FileResponse(html_path)
    return web.Response(text="Learning dashboard not found", status=404)


def setup_learning_routes(app: web.Application) -> None:
    """Register all learning-related routes."""
    # Feedback (authenticated users)
    app.router.add_post("/api/feedback", submit_feedback)
    app.router.add_get("/api/feedback/stats", feedback_stats)

    # Learning dashboard (admin)
    app.router.add_get("/api/admin/learning/pending", learning_pending)
    app.router.add_get("/api/admin/learning/history", learning_history)
    app.router.add_post("/api/admin/learning/{entry_id}/approve", learning_approve)
    app.router.add_post("/api/admin/learning/{entry_id}/reject", learning_reject)
    app.router.add_get("/api/admin/learning/satisfaction-trend", satisfaction_trend)
    app.router.add_get("/api/admin/learning/prompt-versions", prompt_versions)

    # Dashboard page
    app.router.add_get("/admin/learning", learning_page)
