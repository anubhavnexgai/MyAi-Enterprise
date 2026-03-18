"""Tests for the MyAi Super Admin Dashboard AnalyticsService.

Tests use aiosqlite directly to create in-memory test databases with the
required schema and verify that AnalyticsService queries return correct results.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from app.admin.analytics import AnalyticsService


# ── Test Schema ──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role_level TEXT NOT NULL DEFAULT 'employee',
    department TEXT DEFAULT '',
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    user_id TEXT,
    skill_name TEXT,
    confidence REAL,
    response_time_ms INTEGER,
    success BOOLEAN DEFAULT 1,
    error_message TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);
"""


# ── Fixtures ──


@pytest_asyncio.fixture
async def db_path():
    """Create a temporary database file with schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()

    yield path

    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def analytics(db_path):
    """Create an AnalyticsService backed by the test database."""
    mock_database = MagicMock()
    mock_database.db_path = db_path
    return AnalyticsService(mock_database)


def _now_iso():
    return datetime.utcnow().isoformat()


def _hours_ago_iso(hours):
    return (datetime.utcnow() - timedelta(hours=hours)).isoformat()


# ── Tests ──


class TestOverview:
    """Test get_overview() method."""

    @pytest.mark.asyncio
    async def test_overview_empty_db(self, analytics):
        """Returns zeros when no data exists."""
        result = await analytics.get_overview(period_hours=24)

        assert result["total_messages"] == 0
        assert result["total_conversations"] == 0
        assert result["active_users"] == 0
        assert result["total_skill_executions"] == 0
        assert result["avg_response_time_ms"] == 0
        assert result["error_count"] == 0
        assert result["error_rate"] == 0.0
        assert result["period_hours"] == 24

    @pytest.mark.asyncio
    async def test_overview_with_data(self, analytics, db_path):
        """Correctly counts messages, conversations, skill executions, and errors."""
        now = _now_iso()
        two_hours_ago = _hours_ago_iso(2)

        async with aiosqlite.connect(db_path) as db:
            # Insert conversations
            await db.execute(
                "INSERT INTO conversations (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("conv-1", "user-1", two_hours_ago, now),
            )
            await db.execute(
                "INSERT INTO conversations (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("conv-2", "user-2", two_hours_ago, now),
            )

            # Insert messages
            for i in range(5):
                await db.execute(
                    "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                    ("conv-1", "user", f"msg {i}", two_hours_ago),
                )
            for i in range(3):
                await db.execute(
                    "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                    ("conv-2", "user", f"msg {i}", two_hours_ago),
                )

            # Insert usage events
            for i in range(4):
                await db.execute(
                    "INSERT INTO usage_events (event_type, user_id, skill_name, response_time_ms, success, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("skill_execution", "user-1", "it_support", 100 + i * 50, 1, two_hours_ago),
                )
            # Insert an error event
            await db.execute(
                "INSERT INTO usage_events (event_type, user_id, skill_name, response_time_ms, success, error_message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("skill_execution", "user-1", "finance", 500, 0, "Division by zero", two_hours_ago),
            )

            await db.commit()

        result = await analytics.get_overview(period_hours=24)

        assert result["total_messages"] == 8
        assert result["total_conversations"] == 2
        assert result["active_users"] == 2
        assert result["total_skill_executions"] == 5  # 4 success + 1 error
        assert result["avg_response_time_ms"] > 0
        assert result["error_count"] == 1
        assert result["error_rate"] == 20.0  # 1 error out of 5 events

    @pytest.mark.asyncio
    async def test_overview_respects_period(self, analytics, db_path):
        """Data outside the requested period is excluded."""
        now = _now_iso()
        old = _hours_ago_iso(48)

        async with aiosqlite.connect(db_path) as db:
            # Old conversation (outside 24h)
            await db.execute(
                "INSERT INTO conversations (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("conv-old", "user-1", old, old),
            )
            await db.execute(
                "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                ("conv-old", "user", "old message", old),
            )

            # Recent conversation (within 24h)
            await db.execute(
                "INSERT INTO conversations (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("conv-new", "user-2", now, now),
            )
            await db.execute(
                "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                ("conv-new", "user", "new message", now),
            )

            await db.commit()

        result = await analytics.get_overview(period_hours=24)

        assert result["total_messages"] == 1
        assert result["total_conversations"] == 1
        assert result["active_users"] == 1


class TestSkillMetrics:
    """Test get_skill_metrics() method."""

    @pytest.mark.asyncio
    async def test_skill_metrics_empty(self, analytics):
        """Returns empty list when no skill executions exist."""
        result = await analytics.get_skill_metrics(period_hours=168)
        assert result == []

    @pytest.mark.asyncio
    async def test_skill_metrics_aggregation(self, analytics, db_path):
        """Correctly aggregates skill execution data by skill name."""
        now = _now_iso()
        recent = _hours_ago_iso(12)

        async with aiosqlite.connect(db_path) as db:
            # IT Support: 3 executions, all success
            for i in range(3):
                await db.execute(
                    "INSERT INTO usage_events (event_type, user_id, skill_name, confidence, response_time_ms, success, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("skill_execution", "user-1", "it_support", 0.85 + i * 0.05, 100 + i * 100, 1, recent),
                )

            # Finance: 2 executions, 1 success + 1 failure
            await db.execute(
                "INSERT INTO usage_events (event_type, user_id, skill_name, confidence, response_time_ms, success, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("skill_execution", "user-1", "finance", 0.9, 200, 1, recent),
            )
            await db.execute(
                "INSERT INTO usage_events (event_type, user_id, skill_name, confidence, response_time_ms, success, error_message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("skill_execution", "user-2", "finance", 0.7, 500, 0, "API timeout", recent),
            )

            await db.commit()

        result = await analytics.get_skill_metrics(period_hours=168)

        assert len(result) == 2

        # Results are ordered by execution_count DESC
        it_support = next(s for s in result if s["skill_name"] == "it_support")
        finance = next(s for s in result if s["skill_name"] == "finance")

        assert it_support["execution_count"] == 3
        assert it_support["success_rate"] == 100.0
        assert it_support["avg_confidence"] > 0

        assert finance["execution_count"] == 2
        assert finance["success_rate"] == 50.0


class TestUserActivity:
    """Test get_user_activity() method."""

    @pytest.mark.asyncio
    async def test_user_activity_with_users_table(self, analytics, db_path):
        """Returns user activity data when the users table exists."""
        now = _now_iso()
        recent = _hours_ago_iso(12)

        async with aiosqlite.connect(db_path) as db:
            # Create users
            await db.execute(
                "INSERT INTO users (id, email, display_name, role_level, created_at) VALUES (?, ?, ?, ?, ?)",
                ("user-1", "alice@example.com", "Alice", "admin", recent),
            )
            await db.execute(
                "INSERT INTO users (id, email, display_name, role_level, created_at) VALUES (?, ?, ?, ?, ?)",
                ("user-2", "bob@example.com", "Bob", "employee", recent),
            )

            # Create conversation and messages for user-1
            await db.execute(
                "INSERT INTO conversations (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("conv-1", "user-1", recent, now),
            )
            for i in range(5):
                await db.execute(
                    "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                    ("conv-1", "user", f"msg {i}", recent),
                )

            await db.commit()

        result = await analytics.get_user_activity(period_hours=168, limit=50)

        assert len(result) >= 1
        alice = next((u for u in result if u["user_id"] == "user-1"), None)
        assert alice is not None
        assert alice["display_name"] == "Alice"
        assert alice["email"] == "alice@example.com"
        assert alice["role_level"] == "admin"
        assert alice["message_count"] == 5


class TestResponseTimeDistribution:
    """Test get_response_time_distribution() method."""

    @pytest.mark.asyncio
    async def test_response_time_empty(self, analytics):
        """Returns zeros when no response time data exists."""
        result = await analytics.get_response_time_distribution(period_hours=168)
        assert result["p50"] == 0
        assert result["avg"] == 0
        assert result["min"] == 0
        assert result["max"] == 0

    @pytest.mark.asyncio
    async def test_response_time_distribution(self, analytics, db_path):
        """Correctly computes percentiles from response time data."""
        recent = _hours_ago_iso(12)

        async with aiosqlite.connect(db_path) as db:
            # Insert 100 events with response times 1..100
            for i in range(1, 101):
                await db.execute(
                    "INSERT INTO usage_events (event_type, response_time_ms, success, created_at) VALUES (?, ?, ?, ?)",
                    ("skill_execution", i, 1, recent),
                )
            await db.commit()

        result = await analytics.get_response_time_distribution(period_hours=168)

        assert result["min"] == 1
        assert result["max"] == 100
        assert result["avg"] == 50.5
        # P50 should be around 50
        assert 49 <= result["p50"] <= 51
        # P90 should be around 90
        assert 89 <= result["p90"] <= 91
        # P99 should be around 99
        assert 98 <= result["p99"] <= 100


class TestRecentErrors:
    """Test get_recent_errors() method."""

    @pytest.mark.asyncio
    async def test_recent_errors_empty(self, analytics):
        """Returns empty list when no errors exist."""
        result = await analytics.get_recent_errors(limit=50)
        assert result == []

    @pytest.mark.asyncio
    async def test_recent_errors_returns_failures(self, analytics, db_path):
        """Returns error events sorted by most recent first."""
        recent = _hours_ago_iso(1)
        older = _hours_ago_iso(6)

        async with aiosqlite.connect(db_path) as db:
            # Success event (should NOT appear)
            await db.execute(
                "INSERT INTO usage_events (event_type, user_id, skill_name, success, created_at) VALUES (?, ?, ?, ?, ?)",
                ("skill_execution", "user-1", "hr_ops", 1, recent),
            )

            # Error events (should appear)
            await db.execute(
                "INSERT INTO usage_events (event_type, user_id, skill_name, success, error_message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("skill_execution", "user-1", "finance", 0, "Division by zero", older),
            )
            await db.execute(
                "INSERT INTO usage_events (event_type, user_id, skill_name, success, error_message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("error", "user-2", "it_support", 0, "Connection timeout", recent),
            )

            await db.commit()

        result = await analytics.get_recent_errors(limit=50)

        assert len(result) == 2
        # Most recent first
        assert result[0]["skill_name"] == "it_support"
        assert result[0]["error_message"] == "Connection timeout"
        assert result[1]["skill_name"] == "finance"
        assert result[1]["error_message"] == "Division by zero"

    @pytest.mark.asyncio
    async def test_recent_errors_respects_limit(self, analytics, db_path):
        """Limit parameter restricts the number of results."""
        recent = _hours_ago_iso(1)

        async with aiosqlite.connect(db_path) as db:
            for i in range(10):
                await db.execute(
                    "INSERT INTO usage_events (event_type, user_id, success, error_message, created_at) VALUES (?, ?, ?, ?, ?)",
                    ("error", "user-1", 0, f"Error {i}", recent),
                )
            await db.commit()

        result = await analytics.get_recent_errors(limit=3)
        assert len(result) == 3


class TestConversationVolume:
    """Test get_conversation_volume() method."""

    @pytest.mark.asyncio
    async def test_volume_empty(self, analytics):
        """Returns empty list when no messages exist."""
        result = await analytics.get_conversation_volume(period_hours=168)
        assert result == []

    @pytest.mark.asyncio
    async def test_volume_bucketed(self, analytics, db_path):
        """Groups messages into time buckets."""
        recent = _hours_ago_iso(2)

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO conversations (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("conv-1", "user-1", recent, recent),
            )
            for i in range(5):
                await db.execute(
                    "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                    ("conv-1", "user", f"msg {i}", recent),
                )
            await db.commit()

        result = await analytics.get_conversation_volume(period_hours=168, bucket="hourly")

        assert len(result) >= 1
        total_messages = sum(r["message_count"] for r in result)
        assert total_messages == 5


class TestSystemHealth:
    """Test get_system_health() method."""

    @pytest.mark.asyncio
    async def test_system_health_basic(self, analytics):
        """Returns system health metrics even with empty database."""
        result = await analytics.get_system_health()

        assert "uptime_seconds" in result
        assert result["uptime_seconds"] >= 0
        assert "db_size_bytes" in result
        assert result["db_size_bytes"] >= 0
        assert result["total_users"] == 0
        assert result["total_conversations"] == 0
        assert result["total_messages"] == 0

    @pytest.mark.asyncio
    async def test_system_health_with_data(self, analytics, db_path):
        """Counts are accurate when data exists."""
        now = _now_iso()

        async with aiosqlite.connect(db_path) as db:
            # Add users
            await db.execute(
                "INSERT INTO users (id, email, display_name, role_level, created_at) VALUES (?, ?, ?, ?, ?)",
                ("user-1", "a@b.com", "Alice", "admin", now),
            )
            await db.execute(
                "INSERT INTO users (id, email, display_name, role_level, created_at) VALUES (?, ?, ?, ?, ?)",
                ("user-2", "b@c.com", "Bob", "employee", now),
            )

            # Add conversations and messages
            await db.execute(
                "INSERT INTO conversations (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("conv-1", "user-1", now, now),
            )
            await db.execute(
                "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                ("conv-1", "user", "hello", now),
            )
            await db.execute(
                "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                ("conv-1", "assistant", "hi", now),
            )

            await db.commit()

        result = await analytics.get_system_health()

        assert result["total_users"] == 2
        assert result["total_conversations"] == 1
        assert result["total_messages"] == 2
        assert result["db_size_bytes"] > 0


class TestGracefulDegradation:
    """Test that analytics handles missing tables gracefully."""

    @pytest.mark.asyncio
    async def test_overview_no_usage_events_table(self):
        """Returns zeros when usage_events table does not exist."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            async with aiosqlite.connect(path) as db:
                # Only create conversations and messages, not usage_events
                await db.execute("""
                    CREATE TABLE conversations (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                await db.execute("""
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        tool_name TEXT,
                        timestamp TEXT NOT NULL
                    )
                """)
                await db.commit()

            mock_db = MagicMock()
            mock_db.db_path = path
            svc = AnalyticsService(mock_db)

            result = await svc.get_overview(period_hours=24)
            assert result["total_skill_executions"] == 0
            assert result["error_count"] == 0
            assert result["error_rate"] == 0.0

            errors = await svc.get_recent_errors(limit=50)
            assert errors == []

            dist = await svc.get_response_time_distribution(period_hours=168)
            assert dist["p50"] == 0

        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_skill_metrics_no_usage_events_table(self):
        """Returns empty list when usage_events table does not exist."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            async with aiosqlite.connect(path) as db:
                await db.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY)")
                await db.commit()

            mock_db = MagicMock()
            mock_db.db_path = path
            svc = AnalyticsService(mock_db)

            result = await svc.get_skill_metrics(period_hours=168)
            assert result == []
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
