"""HTTP route handlers for the MyAi Super Admin Dashboard.

All handlers are async functions that take aiohttp.web.Request and return
aiohttp.web.Response. They authenticate via Bearer token, check admin
permissions, and delegate to AnalyticsService for data.

These routes will be registered in main.py by the Phase 1 agent or manually
after both phases complete.

Registration example in main.py:
    from app.admin.routes import setup_admin_routes
    setup_admin_routes(app)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger("miai.admin.routes")


async def _get_authenticated_admin(req: web.Request) -> tuple:
    """Extract token, validate session, check admin role.

    Returns (user, None) on success or (None, web.Response) on failure.
    """
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, web.json_response(
            {"error": "Missing or invalid Authorization header"},
            status=401,
        )

    token = auth_header[7:]

    auth = req.app.get("auth_service")
    rbac = req.app.get("rbac_service")

    if auth is None or rbac is None:
        return None, web.json_response(
            {"error": "Auth services not configured"},
            status=503,
        )

    # Validate session token
    user = await auth.validate_session(token)
    if user is None:
        return None, web.json_response(
            {"error": "Invalid or expired session token"},
            status=401,
        )

    # Check admin permission
    if not rbac.is_admin(user):
        return None, web.json_response(
            {"error": "Insufficient permissions. Admin access required."},
            status=403,
        )

    return user, None


# ── Analytics Endpoints ──


async def admin_overview(req: web.Request) -> web.Response:
    """GET /api/admin/analytics/overview"""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    analytics = req.app["analytics_service"]
    period = int(req.query.get("period_hours", "24"))
    data = await analytics.get_overview(period_hours=period)
    return web.json_response(data)


async def admin_skill_metrics(req: web.Request) -> web.Response:
    """GET /api/admin/analytics/skills"""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    analytics = req.app["analytics_service"]
    period = int(req.query.get("period_hours", "168"))
    data = await analytics.get_skill_metrics(period_hours=period)
    return web.json_response({"skills": data})


async def admin_user_activity(req: web.Request) -> web.Response:
    """GET /api/admin/analytics/users"""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    analytics = req.app["analytics_service"]
    period = int(req.query.get("period_hours", "168"))
    limit = int(req.query.get("limit", "50"))
    data = await analytics.get_user_activity(period_hours=period, limit=limit)
    return web.json_response({"users": data})


async def admin_conversation_volume(req: web.Request) -> web.Response:
    """GET /api/admin/analytics/volume"""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    analytics = req.app["analytics_service"]
    period = int(req.query.get("period_hours", "168"))
    bucket = req.query.get("bucket", "hourly")
    data = await analytics.get_conversation_volume(period_hours=period, bucket=bucket)
    return web.json_response({"volume": data})


async def admin_response_times(req: web.Request) -> web.Response:
    """GET /api/admin/analytics/response-times"""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    analytics = req.app["analytics_service"]
    period = int(req.query.get("period_hours", "168"))
    data = await analytics.get_response_time_distribution(period_hours=period)
    return web.json_response(data)


async def admin_recent_errors(req: web.Request) -> web.Response:
    """GET /api/admin/analytics/errors"""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    analytics = req.app["analytics_service"]
    limit = int(req.query.get("limit", "50"))
    data = await analytics.get_recent_errors(limit=limit)
    return web.json_response({"errors": data})


async def admin_system_health(req: web.Request) -> web.Response:
    """GET /api/admin/analytics/health"""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    analytics = req.app["analytics_service"]
    data = await analytics.get_system_health()
    return web.json_response(data)


# ── User Management Endpoints ──


async def admin_list_users(req: web.Request) -> web.Response:
    """GET /api/admin/users — List all users."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    auth = req.app["auth_service"]
    try:
        users = await auth.list_users()
        return web.json_response({
            "users": [u.to_dict() if hasattr(u, "to_dict") else u for u in users]
        })
    except Exception as e:
        logger.error(f"Error listing users: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def admin_update_user_role(req: web.Request) -> web.Response:
    """PUT /api/admin/users/{user_id}/role — Update a user's role level."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    target_user_id = req.match_info["user_id"]

    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    new_role = body.get("role_level")
    if not new_role:
        return web.json_response(
            {"error": "role_level is required in request body"},
            status=400,
        )

    auth = req.app["auth_service"]
    try:
        updated = await auth.update_user_role(target_user_id, new_role)
        if updated:
            return web.json_response({
                "status": "ok",
                "user_id": target_user_id,
                "role_level": new_role,
            })
        else:
            return web.json_response({"error": "User not found"}, status=404)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        logger.error(f"Error updating user role: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def admin_deactivate_user(req: web.Request) -> web.Response:
    """POST /api/admin/users/{user_id}/deactivate — Deactivate a user."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    target_user_id = req.match_info["user_id"]

    auth = req.app["auth_service"]
    try:
        result = await auth.set_user_active(target_user_id, False)
        if result:
            return web.json_response({
                "status": "ok",
                "user_id": target_user_id,
                "is_active": False,
            })
        else:
            return web.json_response({"error": "User not found"}, status=404)
    except Exception as e:
        logger.error(f"Error deactivating user: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def admin_activate_user(req: web.Request) -> web.Response:
    """POST /api/admin/users/{user_id}/activate — Activate a user."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    target_user_id = req.match_info["user_id"]

    auth = req.app["auth_service"]
    try:
        result = await auth.set_user_active(target_user_id, True)
        if result:
            return web.json_response({
                "status": "ok",
                "user_id": target_user_id,
                "is_active": True,
            })
        else:
            return web.json_response({"error": "User not found"}, status=404)
    except Exception as e:
        logger.error(f"Error activating user: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# ── Admin page serving ──


async def admin_page(req: web.Request) -> web.FileResponse:
    """Serve the admin dashboard HTML page."""
    return web.FileResponse(Path(__file__).parent.parent.parent / "web" / "admin.html")


# ── Route registration helper ──


def setup_admin_routes(app: web.Application) -> None:
    """Register all admin dashboard routes on the given aiohttp Application.

    Call this from main.py after setting up auth and analytics services:
        app["analytics_service"] = AnalyticsService(database)
        app["auth_service"] = auth_service
        app["rbac_service"] = rbac_service
        setup_admin_routes(app)
    """
    # Admin page
    app.router.add_get("/admin", admin_page)

    # Analytics API
    app.router.add_get("/api/admin/analytics/overview", admin_overview)
    app.router.add_get("/api/admin/analytics/skills", admin_skill_metrics)
    app.router.add_get("/api/admin/analytics/users", admin_user_activity)
    app.router.add_get("/api/admin/analytics/volume", admin_conversation_volume)
    app.router.add_get("/api/admin/analytics/response-times", admin_response_times)
    app.router.add_get("/api/admin/analytics/errors", admin_recent_errors)
    app.router.add_get("/api/admin/analytics/health", admin_system_health)

    # User management API
    app.router.add_get("/api/admin/users", admin_list_users)
    app.router.add_put("/api/admin/users/{user_id}/role", admin_update_user_role)
    app.router.add_post("/api/admin/users/{user_id}/deactivate", admin_deactivate_user)
    app.router.add_post("/api/admin/users/{user_id}/activate", admin_activate_user)
