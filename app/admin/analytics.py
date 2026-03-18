"""Analytics service for the MyAi Super Admin Dashboard.

Queries the SQLite database for dashboard metrics including usage stats,
skill performance, user activity, and system health.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

logger = logging.getLogger("miai.admin.analytics")

# Track server start time for uptime calculation
_server_start_time = time.time()


class AnalyticsService:
    """Provides analytics queries for the admin dashboard."""

    def __init__(self, database):
        """Initialize with a Database instance (has db_path attribute)."""
        self.database = database
        self.db_path = database.db_path

    async def _table_exists(self, db: aiosqlite.Connection, table_name: str) -> bool:
        """Check if a table exists in the database."""
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def get_overview(self, period_hours: int = 24) -> dict:
        """Get high-level dashboard metrics for the given time period.

        Returns dict with: total_messages, total_conversations, active_users,
        total_skill_executions, avg_response_time_ms, error_count, error_rate.
        """
        cutoff = (datetime.utcnow() - timedelta(hours=period_hours)).isoformat()
        result = {
            "total_messages": 0,
            "total_conversations": 0,
            "active_users": 0,
            "total_skill_executions": 0,
            "avg_response_time_ms": 0,
            "error_count": 0,
            "error_rate": 0.0,
            "period_hours": period_hours,
        }

        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Total messages in period
                if await self._table_exists(db, "messages"):
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM messages WHERE timestamp >= ?",
                        (cutoff,),
                    )
                    row = await cursor.fetchone()
                    result["total_messages"] = row[0] if row else 0

                # Total conversations in period
                if await self._table_exists(db, "conversations"):
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM conversations WHERE created_at >= ?",
                        (cutoff,),
                    )
                    row = await cursor.fetchone()
                    result["total_conversations"] = row[0] if row else 0

                    # Active users (distinct user_ids from conversations in period)
                    cursor = await db.execute(
                        "SELECT COUNT(DISTINCT user_id) FROM conversations WHERE updated_at >= ?",
                        (cutoff,),
                    )
                    row = await cursor.fetchone()
                    result["active_users"] = row[0] if row else 0

                # Usage events metrics
                if await self._table_exists(db, "usage_events"):
                    # Skill executions
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM usage_events WHERE event_type = 'skill_execution' AND created_at >= ?",
                        (cutoff,),
                    )
                    row = await cursor.fetchone()
                    result["total_skill_executions"] = row[0] if row else 0

                    # Average response time
                    cursor = await db.execute(
                        "SELECT AVG(response_time_ms) FROM usage_events WHERE response_time_ms IS NOT NULL AND created_at >= ?",
                        (cutoff,),
                    )
                    row = await cursor.fetchone()
                    result["avg_response_time_ms"] = round(row[0], 1) if row and row[0] else 0

                    # Error count
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM usage_events WHERE success = 0 AND created_at >= ?",
                        (cutoff,),
                    )
                    row = await cursor.fetchone()
                    result["error_count"] = row[0] if row else 0

                    # Total events for error rate calculation
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM usage_events WHERE created_at >= ?",
                        (cutoff,),
                    )
                    row = await cursor.fetchone()
                    total_events = row[0] if row else 0

                    if total_events > 0:
                        result["error_rate"] = round(
                            (result["error_count"] / total_events) * 100, 2
                        )

        except Exception as e:
            logger.error(f"Error fetching overview metrics: {e}", exc_info=True)

        return result

    async def get_skill_metrics(self, period_hours: int = 168) -> list[dict]:
        """Get per-skill execution metrics for the given time period.

        Returns list of dicts with: skill_name, execution_count, avg_confidence,
        avg_response_time_ms, success_rate, thumbs_up, thumbs_down.
        """
        cutoff = (datetime.utcnow() - timedelta(hours=period_hours)).isoformat()
        results = []

        try:
            async with aiosqlite.connect(self.db_path) as db:
                if not await self._table_exists(db, "usage_events"):
                    return results

                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """
                    SELECT
                        skill_name,
                        COUNT(*) as execution_count,
                        AVG(confidence) as avg_confidence,
                        AVG(response_time_ms) as avg_response_time_ms,
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count,
                        COUNT(*) as total_count
                    FROM usage_events
                    WHERE event_type = 'skill_execution'
                      AND skill_name IS NOT NULL
                      AND created_at >= ?
                    GROUP BY skill_name
                    ORDER BY execution_count DESC
                    """,
                    (cutoff,),
                )
                rows = await cursor.fetchall()

                for row in rows:
                    total = row["total_count"]
                    success = row["success_count"]
                    success_rate = round((success / total) * 100, 1) if total > 0 else 0.0

                    # Get thumbs up/down from metadata if available
                    thumbs_up = 0
                    thumbs_down = 0
                    try:
                        fb_cursor = await db.execute(
                            """
                            SELECT
                                SUM(CASE WHEN event_type = 'feedback' AND json_extract(metadata, '$.rating') = 'up' THEN 1 ELSE 0 END) as thumbs_up,
                                SUM(CASE WHEN event_type = 'feedback' AND json_extract(metadata, '$.rating') = 'down' THEN 1 ELSE 0 END) as thumbs_down
                            FROM usage_events
                            WHERE skill_name = ? AND created_at >= ?
                            """,
                            (row["skill_name"], cutoff),
                        )
                        fb_row = await fb_cursor.fetchone()
                        if fb_row:
                            thumbs_up = fb_row["thumbs_up"] or 0
                            thumbs_down = fb_row["thumbs_down"] or 0
                    except Exception:
                        pass

                    results.append({
                        "skill_name": row["skill_name"],
                        "execution_count": row["execution_count"],
                        "avg_confidence": round(row["avg_confidence"], 3) if row["avg_confidence"] else 0.0,
                        "avg_response_time_ms": round(row["avg_response_time_ms"], 1) if row["avg_response_time_ms"] else 0,
                        "success_rate": success_rate,
                        "thumbs_up": thumbs_up,
                        "thumbs_down": thumbs_down,
                    })

        except Exception as e:
            logger.error(f"Error fetching skill metrics: {e}", exc_info=True)

        return results

    async def get_user_activity(self, period_hours: int = 168, limit: int = 50) -> list[dict]:
        """Get user activity metrics for the given time period.

        Returns list of dicts with: user_id, display_name, email, role_level,
        message_count, last_active, skill_usage (JSON string).
        """
        cutoff = (datetime.utcnow() - timedelta(hours=period_hours)).isoformat()
        results = []

        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row

                has_users = await self._table_exists(db, "users")
                has_conversations = await self._table_exists(db, "conversations")
                has_messages = await self._table_exists(db, "messages")

                if has_users:
                    # Build a query that joins users with their activity
                    query = """
                        SELECT
                            u.id as user_id,
                            u.display_name,
                            u.email,
                            u.role_level,
                            COALESCE(activity.message_count, 0) as message_count,
                            COALESCE(activity.last_active, u.created_at) as last_active
                        FROM users u
                        LEFT JOIN (
                            SELECT
                                c.user_id,
                                COUNT(m.id) as message_count,
                                MAX(m.timestamp) as last_active
                            FROM conversations c
                            LEFT JOIN messages m ON m.conversation_id = c.id AND m.timestamp >= ?
                            WHERE c.updated_at >= ?
                            GROUP BY c.user_id
                        ) activity ON activity.user_id = u.id
                        ORDER BY activity.message_count DESC NULLS LAST
                        LIMIT ?
                    """
                    cursor = await db.execute(query, (cutoff, cutoff, limit))
                    rows = await cursor.fetchall()

                    for row in rows:
                        # Get skill usage breakdown for this user
                        skill_usage = "{}"
                        if await self._table_exists(db, "usage_events"):
                            try:
                                sk_cursor = await db.execute(
                                    """
                                    SELECT skill_name, COUNT(*) as cnt
                                    FROM usage_events
                                    WHERE user_id = ? AND event_type = 'skill_execution'
                                      AND skill_name IS NOT NULL AND created_at >= ?
                                    GROUP BY skill_name
                                    """,
                                    (row["user_id"], cutoff),
                                )
                                sk_rows = await sk_cursor.fetchall()
                                import json
                                skill_usage = json.dumps(
                                    {r["skill_name"]: r["cnt"] for r in sk_rows}
                                )
                            except Exception:
                                pass

                        results.append({
                            "user_id": row["user_id"],
                            "display_name": row["display_name"],
                            "email": row["email"],
                            "role_level": row["role_level"],
                            "message_count": row["message_count"],
                            "last_active": row["last_active"],
                            "skill_usage": skill_usage,
                        })

                elif has_conversations and has_messages:
                    # Fallback: no users table, use conversations
                    query = """
                        SELECT
                            c.user_id,
                            COUNT(m.id) as message_count,
                            MAX(m.timestamp) as last_active
                        FROM conversations c
                        LEFT JOIN messages m ON m.conversation_id = c.id AND m.timestamp >= ?
                        WHERE c.updated_at >= ?
                        GROUP BY c.user_id
                        ORDER BY message_count DESC
                        LIMIT ?
                    """
                    cursor = await db.execute(query, (cutoff, cutoff, limit))
                    rows = await cursor.fetchall()

                    for row in rows:
                        results.append({
                            "user_id": row["user_id"],
                            "display_name": row["user_id"],
                            "email": "",
                            "role_level": "employee",
                            "message_count": row["message_count"],
                            "last_active": row["last_active"] or "",
                            "skill_usage": "{}",
                        })

        except Exception as e:
            logger.error(f"Error fetching user activity: {e}", exc_info=True)

        return results

    async def get_conversation_volume(
        self, period_hours: int = 168, bucket: str = "hourly"
    ) -> list[dict]:
        """Get conversation volume bucketed by time.

        Args:
            period_hours: Number of hours to look back.
            bucket: 'hourly' or 'daily'.

        Returns list of dicts with: bucket_time, message_count, conversation_count.
        """
        cutoff = (datetime.utcnow() - timedelta(hours=period_hours)).isoformat()
        results = []

        # SQLite strftime format for bucketing
        if bucket == "daily":
            time_format = "%Y-%m-%d"
        else:
            time_format = "%Y-%m-%d %H:00"

        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row

                has_messages = await self._table_exists(db, "messages")
                has_conversations = await self._table_exists(db, "conversations")

                if has_messages:
                    query = """
                        SELECT
                            strftime(?, m.timestamp) as bucket_time,
                            COUNT(m.id) as message_count,
                            COUNT(DISTINCT m.conversation_id) as conversation_count
                        FROM messages m
                        WHERE m.timestamp >= ?
                        GROUP BY bucket_time
                        ORDER BY bucket_time ASC
                    """
                    cursor = await db.execute(query, (time_format, cutoff))
                    rows = await cursor.fetchall()

                    for row in rows:
                        results.append({
                            "bucket_time": row["bucket_time"],
                            "message_count": row["message_count"],
                            "conversation_count": row["conversation_count"],
                        })

        except Exception as e:
            logger.error(f"Error fetching conversation volume: {e}", exc_info=True)

        return results

    async def get_response_time_distribution(self, period_hours: int = 168) -> dict:
        """Get response time percentile distribution.

        Returns dict with: p50, p75, p90, p95, p99, avg, min, max (all in ms).
        """
        cutoff = (datetime.utcnow() - timedelta(hours=period_hours)).isoformat()
        result = {
            "p50": 0, "p75": 0, "p90": 0, "p95": 0, "p99": 0,
            "avg": 0, "min": 0, "max": 0,
        }

        try:
            async with aiosqlite.connect(self.db_path) as db:
                if not await self._table_exists(db, "usage_events"):
                    return result

                # Get all response times sorted for percentile calculation
                cursor = await db.execute(
                    """
                    SELECT response_time_ms
                    FROM usage_events
                    WHERE response_time_ms IS NOT NULL AND created_at >= ?
                    ORDER BY response_time_ms ASC
                    """,
                    (cutoff,),
                )
                rows = await cursor.fetchall()

                if not rows:
                    return result

                times = [r[0] for r in rows]
                n = len(times)

                def percentile(data: list, pct: float) -> int:
                    idx = int(pct / 100.0 * (len(data) - 1))
                    return data[idx]

                result["p50"] = percentile(times, 50)
                result["p75"] = percentile(times, 75)
                result["p90"] = percentile(times, 90)
                result["p95"] = percentile(times, 95)
                result["p99"] = percentile(times, 99)
                result["avg"] = round(sum(times) / n, 1)
                result["min"] = times[0]
                result["max"] = times[-1]

        except Exception as e:
            logger.error(f"Error fetching response time distribution: {e}", exc_info=True)

        return result

    async def get_recent_errors(self, limit: int = 50) -> list[dict]:
        """Get recent error events.

        Returns list of dicts with: timestamp, user_id, event_type, error_message, skill_name.
        """
        results = []

        try:
            async with aiosqlite.connect(self.db_path) as db:
                if not await self._table_exists(db, "usage_events"):
                    return results

                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """
                    SELECT created_at, user_id, event_type, error_message, skill_name
                    FROM usage_events
                    WHERE success = 0
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = await cursor.fetchall()

                for row in rows:
                    results.append({
                        "timestamp": row["created_at"],
                        "user_id": row["user_id"] or "",
                        "event_type": row["event_type"],
                        "error_message": row["error_message"] or "",
                        "skill_name": row["skill_name"] or "",
                    })

        except Exception as e:
            logger.error(f"Error fetching recent errors: {e}", exc_info=True)

        return results

    async def get_system_health(self) -> dict:
        """Get system health metrics.

        Returns dict with: uptime_seconds, db_size_bytes, total_users,
        total_conversations, total_messages, index_count.
        """
        result = {
            "uptime_seconds": round(time.time() - _server_start_time),
            "db_size_bytes": 0,
            "total_users": 0,
            "total_conversations": 0,
            "total_messages": 0,
            "index_count": 0,
        }

        # Database file size
        try:
            db_file = Path(self.db_path)
            if db_file.exists():
                result["db_size_bytes"] = db_file.stat().st_size
        except Exception:
            pass

        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Total users
                if await self._table_exists(db, "users"):
                    cursor = await db.execute("SELECT COUNT(*) FROM users")
                    row = await cursor.fetchone()
                    result["total_users"] = row[0] if row else 0

                # Total conversations
                if await self._table_exists(db, "conversations"):
                    cursor = await db.execute("SELECT COUNT(*) FROM conversations")
                    row = await cursor.fetchone()
                    result["total_conversations"] = row[0] if row else 0

                # Total messages
                if await self._table_exists(db, "messages"):
                    cursor = await db.execute("SELECT COUNT(*) FROM messages")
                    row = await cursor.fetchone()
                    result["total_messages"] = row[0] if row else 0

        except Exception as e:
            logger.error(f"Error fetching system health: {e}", exc_info=True)

        # ChromaDB index count
        try:
            from app.config import settings
            chroma_path = Path(settings.chroma_path)
            if chroma_path.exists():
                # Count collection directories as a proxy for index count
                result["index_count"] = sum(
                    1 for p in chroma_path.iterdir() if p.is_dir()
                )
        except Exception:
            pass

        return result
