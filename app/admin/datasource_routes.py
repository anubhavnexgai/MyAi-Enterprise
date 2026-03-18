"""HTTP route handlers for Phase 3 — Company Data Source management.

Provides CRUD endpoints for data sources, connection testing, and
indexing triggers. All endpoints require admin+ authentication.

Registration example in main.py:
    from app.admin.datasource_routes import setup_datasource_routes
    setup_datasource_routes(app)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from aiohttp import web

from app.admin.routes import _get_authenticated_admin

logger = logging.getLogger("miai.admin.datasource_routes")

VALID_SOURCE_TYPES = {"local_directory", "sql_database", "sharepoint", "rest_api"}
VALID_ROLE_LEVELS = {"employee", "manager", "admin", "super_admin"}

CONNECTOR_MAP = {
    "local_directory": "app.datasources.local_directory.LocalDirectoryConnector",
    "sql_database": "app.datasources.sql_database.SqlDatabaseConnector",
    "sharepoint": "app.datasources.sharepoint.SharePointConnector",
    "rest_api": "app.datasources.rest_api.RestApiConnector",
}


def _import_connector(source_type: str):
    """Dynamically import and return the connector class for a source type."""
    module_path = CONNECTOR_MAP.get(source_type)
    if not module_path:
        return None
    parts = module_path.rsplit(".", 1)
    module_name, class_name = parts[0], parts[1]
    import importlib
    mod = importlib.import_module(module_name)
    return getattr(mod, class_name)


# ── List Data Sources ──


async def list_datasources(req: web.Request) -> web.Response:
    """GET /api/admin/datasources — List all data sources."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    db = req.app["database"]
    try:
        rows = await db.fetch_all(
            "SELECT id, name, source_type, min_role_level, status, "
            "document_count, last_indexed_at, created_at, updated_at "
            "FROM data_sources ORDER BY created_at DESC"
        )
        sources = []
        for r in rows:
            sources.append({
                "id": r["id"],
                "name": r["name"],
                "source_type": r["source_type"],
                "min_role_level": r["min_role_level"],
                "status": r["status"],
                "document_count": r["document_count"],
                "last_indexed_at": r["last_indexed_at"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return web.json_response({"datasources": sources})
    except Exception as e:
        logger.error(f"Error listing datasources: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# ── Create Data Source ──


async def create_datasource(req: web.Request) -> web.Response:
    """POST /api/admin/datasources — Create a new data source."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    name = body.get("name")
    source_type = body.get("source_type")
    config = body.get("config", {})
    min_role_level = body.get("min_role_level", "employee")

    if not name or not source_type:
        return web.json_response(
            {"error": "name and source_type are required"}, status=400
        )

    if source_type not in VALID_SOURCE_TYPES:
        return web.json_response(
            {"error": f"Invalid source_type. Must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}"},
            status=400,
        )

    if min_role_level not in VALID_ROLE_LEVELS:
        return web.json_response(
            {"error": f"Invalid min_role_level. Must be one of: {', '.join(sorted(VALID_ROLE_LEVELS))}"},
            status=400,
        )

    db = req.app["database"]
    encryption = req.app["encryption"]

    try:
        source_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        encrypted_config = encryption.encrypt(json.dumps(config))

        await db.execute(
            "INSERT INTO data_sources (id, name, source_type, config_encrypted, "
            "min_role_level, status, document_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, name, source_type, encrypted_config,
             min_role_level, "pending", 0, now, now),
        )

        return web.json_response({
            "status": "ok",
            "datasource": {
                "id": source_id,
                "name": name,
                "source_type": source_type,
                "min_role_level": min_role_level,
                "status": "pending",
                "document_count": 0,
                "created_at": now,
                "updated_at": now,
            },
        }, status=201)
    except Exception as e:
        logger.error(f"Error creating datasource: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# ── Get Single Data Source ──


async def get_datasource(req: web.Request) -> web.Response:
    """GET /api/admin/datasources/{id} — Get a single data source."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    source_id = req.match_info["id"]
    db = req.app["database"]
    encryption = req.app["encryption"]

    try:
        row = await db.fetch_one(
            "SELECT id, name, source_type, config_encrypted, min_role_level, "
            "status, document_count, last_indexed_at, created_at, updated_at "
            "FROM data_sources WHERE id = ?",
            (source_id,),
        )
        if not row:
            return web.json_response({"error": "Data source not found"}, status=404)

        config = {}
        if row["config_encrypted"]:
            try:
                config = json.loads(encryption.decrypt(row["config_encrypted"]))
            except Exception:
                config = {}

        return web.json_response({
            "datasource": {
                "id": row["id"],
                "name": row["name"],
                "source_type": row["source_type"],
                "config": config,
                "min_role_level": row["min_role_level"],
                "status": row["status"],
                "document_count": row["document_count"],
                "last_indexed_at": row["last_indexed_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        })
    except Exception as e:
        logger.error(f"Error getting datasource: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# ── Update Data Source ──


async def update_datasource(req: web.Request) -> web.Response:
    """PUT /api/admin/datasources/{id} — Update a data source."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    source_id = req.match_info["id"]

    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    db = req.app["database"]
    encryption = req.app["encryption"]

    try:
        existing = await db.fetch_one(
            "SELECT id FROM data_sources WHERE id = ?", (source_id,)
        )
        if not existing:
            return web.json_response({"error": "Data source not found"}, status=404)

        now = datetime.now(timezone.utc).isoformat()
        updates = []
        params = []

        if "name" in body:
            updates.append("name = ?")
            params.append(body["name"])

        if "source_type" in body:
            if body["source_type"] not in VALID_SOURCE_TYPES:
                return web.json_response(
                    {"error": f"Invalid source_type. Must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}"},
                    status=400,
                )
            updates.append("source_type = ?")
            params.append(body["source_type"])

        if "config" in body:
            encrypted_config = encryption.encrypt(json.dumps(body["config"]))
            updates.append("config_encrypted = ?")
            params.append(encrypted_config)

        if "min_role_level" in body:
            if body["min_role_level"] not in VALID_ROLE_LEVELS:
                return web.json_response(
                    {"error": f"Invalid min_role_level. Must be one of: {', '.join(sorted(VALID_ROLE_LEVELS))}"},
                    status=400,
                )
            updates.append("min_role_level = ?")
            params.append(body["min_role_level"])

        if not updates:
            return web.json_response(
                {"error": "No valid fields to update"}, status=400
            )

        updates.append("updated_at = ?")
        params.append(now)
        params.append(source_id)

        await db.execute(
            f"UPDATE data_sources SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )

        return web.json_response({"status": "ok", "id": source_id, "updated_at": now})
    except Exception as e:
        logger.error(f"Error updating datasource: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# ── Delete Data Source ──


async def delete_datasource(req: web.Request) -> web.Response:
    """DELETE /api/admin/datasources/{id} — Delete a data source and its indexed docs."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    source_id = req.match_info["id"]
    db = req.app["database"]

    try:
        existing = await db.fetch_one(
            "SELECT id FROM data_sources WHERE id = ?", (source_id,)
        )
        if not existing:
            return web.json_response({"error": "Data source not found"}, status=404)

        # Delete indexed documents from DB
        await db.execute(
            "DELETE FROM indexed_documents WHERE source_id = ?", (source_id,)
        )

        # Delete chunks from ChromaDB via rag_service
        rag_service = req.app.get("rag_service")
        if rag_service:
            try:
                await rag_service.delete_source_chunks(source_id)
            except Exception as e:
                logger.warning(f"Error deleting ChromaDB chunks for source {source_id}: {e}")

        # Delete the data source record
        await db.execute("DELETE FROM data_sources WHERE id = ?", (source_id,))

        return web.json_response({"status": "ok", "id": source_id})
    except Exception as e:
        logger.error(f"Error deleting datasource: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# ── Test Connection ──


async def test_datasource(req: web.Request) -> web.Response:
    """POST /api/admin/datasources/{id}/test — Test data source connection."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    source_id = req.match_info["id"]
    db = req.app["database"]
    encryption = req.app["encryption"]

    try:
        row = await db.fetch_one(
            "SELECT source_type, config_encrypted FROM data_sources WHERE id = ?",
            (source_id,),
        )
        if not row:
            return web.json_response({"error": "Data source not found"}, status=404)

        config = {}
        if row["config_encrypted"]:
            config = json.loads(encryption.decrypt(row["config_encrypted"]))

        ConnectorClass = _import_connector(row["source_type"])
        if not ConnectorClass:
            return web.json_response(
                {"error": f"No connector found for type: {row['source_type']}"},
                status=400,
            )

        connector = ConnectorClass()
        success, message = await connector.test_connection(config)

        return web.json_response({
            "success": success,
            "message": message,
            "source_id": source_id,
        })
    except Exception as e:
        logger.error(f"Error testing datasource connection: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# ── Trigger Indexing ──


async def index_datasource(req: web.Request) -> web.Response:
    """POST /api/admin/datasources/{id}/index — Trigger re-indexing."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    source_id = req.match_info["id"]
    db = req.app["database"]

    try:
        existing = await db.fetch_one(
            "SELECT id FROM data_sources WHERE id = ?", (source_id,)
        )
        if not existing:
            return web.json_response({"error": "Data source not found"}, status=404)

        indexing_service = req.app["indexing_service"]
        status = await indexing_service.start_indexing(source_id)

        return web.json_response({
            "status": "ok",
            "indexing_status": status,
            "source_id": source_id,
        })
    except Exception as e:
        logger.error(f"Error triggering indexing: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# ── Get Indexing Status ──


async def datasource_status(req: web.Request) -> web.Response:
    """GET /api/admin/datasources/{id}/status — Get indexing status and doc count."""
    user, error_resp = await _get_authenticated_admin(req)
    if error_resp:
        return error_resp

    source_id = req.match_info["id"]
    db = req.app["database"]

    try:
        row = await db.fetch_one(
            "SELECT id, name, status, document_count, last_indexed_at "
            "FROM data_sources WHERE id = ?",
            (source_id,),
        )
        if not row:
            return web.json_response({"error": "Data source not found"}, status=404)

        return web.json_response({
            "source_id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "document_count": row["document_count"],
            "last_indexed_at": row["last_indexed_at"],
        })
    except Exception as e:
        logger.error(f"Error getting datasource status: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# ── Route Registration ──


def setup_datasource_routes(app: web.Application) -> None:
    """Register all data source management routes on the given aiohttp Application.

    Call this from main.py after setting up services:
        app["database"] = database
        app["encryption"] = encryption_service
        app["indexing_service"] = indexing_service
        setup_datasource_routes(app)
    """
    app.router.add_get("/api/admin/datasources", list_datasources)
    app.router.add_post("/api/admin/datasources", create_datasource)
    app.router.add_get("/api/admin/datasources/{id}", get_datasource)
    app.router.add_put("/api/admin/datasources/{id}", update_datasource)
    app.router.add_delete("/api/admin/datasources/{id}", delete_datasource)
    app.router.add_post("/api/admin/datasources/{id}/test", test_datasource)
    app.router.add_post("/api/admin/datasources/{id}/index", index_datasource)
    app.router.add_get("/api/admin/datasources/{id}/status", datasource_status)
