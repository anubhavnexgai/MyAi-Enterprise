from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from app.storage.models import Conversation, Message, Role


class Database:
    def __init__(self, db_path: str = "data/miai.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            # Enable WAL mode for better concurrency
            await db.execute("PRAGMA journal_mode=WAL")

            await db.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Migrate: add title column if missing (for existing databases)
            try:
                await db.execute("SELECT title FROM conversations LIMIT 1")
            except Exception:
                await db.execute("ALTER TABLE conversations ADD COLUMN title TEXT DEFAULT ''")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_name TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_conv
                ON messages(conversation_id)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    directory TEXT NOT NULL,
                    granted_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '',
                    bio TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS meeting_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    meeting_subject TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    key_points TEXT NOT NULL DEFAULT '',
                    ended_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_contexts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, name)
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_history_user
                ON meeting_history(user_id)
            """)

            # ── RBAC / Auth tables ──

            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    role_level TEXT NOT NULL DEFAULT 'employee',
                    department TEXT DEFAULT '',
                    password_hash TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS role_permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role_level TEXT NOT NULL,
                    permission_key TEXT NOT NULL,
                    UNIQUE(role_level, permission_key)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_skill_overrides (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    allowed BOOLEAN NOT NULL DEFAULT 1,
                    UNIQUE(user_id, skill_name)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS file_access_policies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role_level TEXT NOT NULL,
                    directory_path TEXT NOT NULL,
                    access_type TEXT NOT NULL DEFAULT 'read',
                    UNIQUE(role_level, directory_path)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS api_sessions (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)

            # ── Usage Events (analytics) ──

            await db.execute("""
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    user_id TEXT,
                    skill_name TEXT,
                    confidence REAL,
                    response_time_ms INTEGER,
                    success BOOLEAN NOT NULL DEFAULT 1,
                    error_message TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_usage_events_type_created
                ON usage_events(event_type, created_at)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_usage_events_user
                ON usage_events(user_id, created_at)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_usage_events_skill
                ON usage_events(skill_name, created_at)
            """)

            # ── Phase 3: Data Sources & Indexed Documents ──

            await db.execute("""
                CREATE TABLE IF NOT EXISTS data_sources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    config_encrypted TEXT NOT NULL,
                    min_role_level TEXT NOT NULL DEFAULT 'employee',
                    is_active BOOLEAN DEFAULT 1,
                    last_indexed_at TEXT,
                    document_count INTEGER DEFAULT 0,
                    index_status TEXT DEFAULT 'pending',
                    index_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS indexed_documents (
                    id TEXT PRIMARY KEY,
                    data_source_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    chunk_count INTEGER DEFAULT 0,
                    indexed_at TEXT NOT NULL,
                    FOREIGN KEY (data_source_id) REFERENCES data_sources(id),
                    UNIQUE(data_source_id, file_path)
                )
            """)

            # ── NexgAI Session Mapping ──

            await db.execute("""
                CREATE TABLE IF NOT EXISTS nexgai_sessions (
                    myai_user_id TEXT PRIMARY KEY,
                    nexgai_session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL
                )
            """)

            # ── Phase 4: Self-Learning Loop ──

            await db.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    message_id INTEGER NOT NULL,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    rating TEXT NOT NULL,
                    comment TEXT DEFAULT '',
                    source TEXT DEFAULT 'local',
                    agent_name TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_message
                ON feedback(message_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_rating_created
                ON feedback(rating, created_at)
            """)

            # ── Reminders ──

            await db.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    fired INTEGER DEFAULT 0
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS learning_entries (
                    id TEXT PRIMARY KEY,
                    entry_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    agent_name TEXT,
                    trigger_feedback_ids TEXT NOT NULL,
                    original_query TEXT NOT NULL,
                    original_response TEXT NOT NULL,
                    suggested_improvement TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_learning_status
                ON learning_entries(status, created_at)
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS prompt_versions (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 0,
                    learning_entry_id TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS satisfaction_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    total_feedback INTEGER DEFAULT 0,
                    thumbs_up INTEGER DEFAULT 0,
                    thumbs_down INTEGER DEFAULT 0,
                    satisfaction_pct REAL DEFAULT 0.0,
                    UNIQUE(snapshot_date, source)
                )
            """)

            await db.commit()

            # Seed default role permissions
            await self._seed_default_permissions(db)

    async def _seed_default_permissions(self, db: aiosqlite.Connection):
        """Seed default role permissions if they don't exist yet."""
        cursor = await db.execute("SELECT COUNT(*) FROM role_permissions")
        row = await cursor.fetchone()
        if row[0] > 0:
            return  # Already seeded

        default_permissions = {
            "super_admin": [
                "file:*", "admin:*", "data:*",
            ],
            "admin": [
                "file:*", "admin:dashboard", "admin:users",
            ],
            "manager": [
                "file:read",
            ],
            "employee": [
                "file:read",
            ],
        }

        for role_level, perms in default_permissions.items():
            for perm in perms:
                await db.execute(
                    "INSERT OR IGNORE INTO role_permissions (role_level, permission_key) VALUES (?, ?)",
                    (role_level, perm),
                )

        await db.commit()

    # ── Multi-conversation management ──

    async def list_conversations(self, user_id: str) -> list[dict]:
        """Return all conversations for a user, newest first, with a preview of the last message."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            )
            rows = await cursor.fetchall()

            result = []
            for row in rows:
                conv_id = row["id"]
                title = row["title"] if row["title"] else ""

                # Get last message as preview
                msg_cursor = await db.execute(
                    """SELECT content FROM messages
                       WHERE conversation_id = ? AND role IN ('user', 'assistant')
                       ORDER BY id DESC LIMIT 1""",
                    (conv_id,),
                )
                msg_row = await msg_cursor.fetchone()
                preview = ""
                if msg_row:
                    preview = (msg_row["content"] or "")[:50]
                    if len(msg_row["content"] or "") > 50:
                        preview += "..."

                # If no title, use first user message
                if not title:
                    first_cursor = await db.execute(
                        """SELECT content FROM messages
                           WHERE conversation_id = ? AND role = 'user'
                           ORDER BY id ASC LIMIT 1""",
                        (conv_id,),
                    )
                    first_row = await first_cursor.fetchone()
                    if first_row:
                        title = (first_row["content"] or "")[:50]
                        if len(first_row["content"] or "") > 50:
                            title += "..."

                if not title:
                    title = "New Chat"

                result.append({
                    "id": conv_id,
                    "user_id": row["user_id"],
                    "title": title,
                    "preview": preview,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
            return result

    async def create_conversation(self, user_id: str, title: str = "") -> str:
        """Create a new conversation and return its ID."""
        conv_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conv_id, user_id, title, now, now),
            )
            await db.commit()
        return conv_id

    async def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation and all its messages."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            await db.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            await db.commit()

    async def rename_conversation(self, conversation_id: str, title: str) -> None:
        """Update the title of a conversation."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, datetime.utcnow().isoformat(), conversation_id),
            )
            await db.commit()

    async def get_conversation_by_id(self, conversation_id: str) -> Conversation | None:
        """Load a conversation by its ID, including messages."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None

            conv = Conversation(
                id=row["id"],
                user_id=row["user_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            cursor = await db.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp",
                (conv.id,),
            )
            rows = await cursor.fetchall()
            conv.messages = [
                Message(
                    role=Role(r["role"]),
                    content=r["content"],
                    tool_name=r["tool_name"],
                    timestamp=datetime.fromisoformat(r["timestamp"]),
                )
                for r in rows
            ]
            return conv

    async def get_conversation_owner(self, conversation_id: str) -> str | None:
        """Return the user_id that owns this conversation, or None."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_or_create_conversation(self, user_id: str) -> Conversation:
        """Get the latest active conversation for a user, or create one."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                (user_id,),
            )
            row = await cursor.fetchone()

            if row:
                conv = Conversation(
                    id=row["id"],
                    user_id=row["user_id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
                # Load messages
                cursor = await db.execute(
                    "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp",
                    (conv.id,),
                )
                rows = await cursor.fetchall()
                conv.messages = [
                    Message(
                        role=Role(r["role"]),
                        content=r["content"],
                        tool_name=r["tool_name"],
                        timestamp=datetime.fromisoformat(r["timestamp"]),
                    )
                    for r in rows
                ]
                return conv

            # Create new
            conv_id = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()
            await db.execute(
                "INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conv_id, user_id, "", now, now),
            )
            await db.commit()
            return Conversation(id=conv_id, user_id=user_id)

    async def add_message(self, conversation_id: str, message: Message) -> int:
        """Insert a message and return its auto-generated integer ID."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_name, timestamp) VALUES (?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    message.role.value,
                    message.content,
                    message.tool_name,
                    message.timestamp.isoformat(),
                ),
            )
            msg_id = cursor.lastrowid
            await db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), conversation_id),
            )
            await db.commit()
            return msg_id

    async def get_chat_history(self, user_id: str, limit: int = 50, conversation_id: str | None = None) -> list[dict]:
        """Return recent messages for a conversation, ordered oldest-first.

        If conversation_id is given, use that; otherwise use the user's latest conversation.
        Only returns user and assistant messages (skips system/tool).
        Each dict has: id, role, content, timestamp, conversation_id.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            if conversation_id:
                conv_id = conversation_id
            else:
                # Find the latest conversation for this user
                cursor = await db.execute(
                    "SELECT id FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                    (user_id,),
                )
                row = await cursor.fetchone()
                if not row:
                    return []
                conv_id = row["id"]
            cursor = await db.execute(
                """SELECT id, conversation_id, role, content, timestamp
                   FROM messages
                   WHERE conversation_id = ? AND role IN ('user', 'assistant')
                   ORDER BY id DESC LIMIT ?""",
                (conv_id, limit),
            )
            rows = await cursor.fetchall()
            # Reverse so oldest is first
            return [
                {
                    "id": r["id"],
                    "conversation_id": r["conversation_id"],
                    "role": r["role"],
                    "content": r["content"],
                    "timestamp": r["timestamp"],
                }
                for r in reversed(rows)
            ]

    async def clear_conversation(self, user_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM conversations WHERE user_id = ?", (user_id,)
            )
            rows = await cursor.fetchall()
            for row in rows:
                await db.execute(
                    "DELETE FROM messages WHERE conversation_id = ?", (row[0],)
                )
            await db.execute(
                "DELETE FROM conversations WHERE user_id = ?", (user_id,)
            )
            await db.commit()

    # ── User Profiles ──

    async def get_user_profile(self, user_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            if row:
                return {"user_id": row["user_id"], "name": row["name"],
                        "role": row["role"], "bio": row["bio"]}
            return None

    async def set_user_profile(self, user_id: str, name: str = "", role: str = "", bio: str = ""):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO user_profiles (user_id, name, role, bio, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    name = CASE WHEN ? != '' THEN ? ELSE name END,
                    role = CASE WHEN ? != '' THEN ? ELSE role END,
                    bio = CASE WHEN ? != '' THEN ? ELSE bio END,
                    updated_at = ?
            """, (user_id, name, role, bio, datetime.utcnow().isoformat(),
                  name, name, role, role, bio, bio, datetime.utcnow().isoformat()))
            await db.commit()

    # ── Meeting History ──

    async def save_meeting_summary(
        self, user_id: str, call_id: str, meeting_subject: str,
        summary: str, key_points: str
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO meeting_history (user_id, call_id, meeting_subject, summary, key_points, ended_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, call_id, meeting_subject, summary, key_points,
                  datetime.utcnow().isoformat()))
            await db.commit()

    async def get_recent_meetings(self, user_id: str, limit: int = 5) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM meeting_history WHERE user_id = ?
                ORDER BY ended_at DESC LIMIT ?
            """, (user_id, limit))
            rows = await cursor.fetchall()
            return [{"meeting_subject": r["meeting_subject"], "summary": r["summary"],
                     "key_points": r["key_points"], "ended_at": r["ended_at"]}
                    for r in rows]

    # ── User Contexts (knowledge for meeting suggestions) ──

    async def add_context(self, user_id: str, name: str, content: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO user_contexts (user_id, name, content, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, name) DO UPDATE SET
                    content = ?, created_at = ?
            """, (user_id, name, content, datetime.utcnow().isoformat(),
                  content, datetime.utcnow().isoformat()))
            await db.commit()

    async def remove_context(self, user_id: str, name: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM user_contexts WHERE user_id = ? AND name = ?",
                (user_id, name)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_all_contexts(self, user_id: str) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT name, content FROM user_contexts WHERE user_id = ? ORDER BY name",
                (user_id,)
            )
            rows = await cursor.fetchall()
            return [{"name": r["name"], "content": r["content"]} for r in rows]

    # ── Usage Events ──

    async def log_usage_event(
        self,
        event_type: str,
        user_id: str | None = None,
        skill_name: str | None = None,
        confidence: float | None = None,
        response_time_ms: int | None = None,
        success: bool = True,
        error_message: str | None = None,
        metadata: dict | None = None,
    ):
        """Log a usage event for analytics."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO usage_events
                   (event_type, user_id, skill_name, confidence, response_time_ms,
                    success, error_message, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_type,
                    user_id,
                    skill_name,
                    confidence,
                    response_time_ms,
                    1 if success else 0,
                    error_message,
                    json.dumps(metadata) if metadata else None,
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()

    async def save_permission(self, user_id: str, directory: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO permissions (user_id, directory, granted_at) VALUES (?, ?, ?)",
                (user_id, directory, datetime.utcnow().isoformat()),
            )
            await db.commit()

    # ── Data Sources (Phase 3) ──

    async def create_data_source(
        self,
        name: str,
        source_type: str,
        config_encrypted: str,
        min_role_level: str = "employee",
    ) -> str:
        """Create a new data source and return its id."""
        source_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO data_sources
                   (id, name, source_type, config_encrypted, min_role_level,
                    is_active, document_count, index_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, 0, 'pending', ?, ?)""",
                (source_id, name, source_type, config_encrypted, min_role_level, now, now),
            )
            await db.commit()
        return source_id

    async def get_data_source(self, source_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM data_sources WHERE id = ?", (source_id,)
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

    async def list_data_sources(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM data_sources ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_data_source(self, source_id: str, **kwargs) -> None:
        if not kwargs:
            return
        kwargs["updated_at"] = datetime.utcnow().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [source_id]
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE data_sources SET {set_clause} WHERE id = ?", values
            )
            await db.commit()

    async def delete_data_source(self, source_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM indexed_documents WHERE data_source_id = ?",
                (source_id,),
            )
            await db.execute(
                "DELETE FROM data_sources WHERE id = ?", (source_id,)
            )
            await db.commit()

    async def update_indexing_status(
        self,
        source_id: str,
        status: str,
        error: str | None = None,
        document_count: int | None = None,
        last_indexed_at: str | None = None,
    ) -> None:
        kwargs: dict = {
            "index_status": status,
            "index_error": error,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if document_count is not None:
            kwargs["document_count"] = document_count
        if last_indexed_at is not None:
            kwargs["last_indexed_at"] = last_indexed_at

        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [source_id]
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE data_sources SET {set_clause} WHERE id = ?", values
            )
            await db.commit()

    # ── Indexed Documents ──

    async def get_indexed_document(
        self, source_id: str, file_path: str
    ) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM indexed_documents WHERE data_source_id = ? AND file_path = ?",
                (source_id, file_path),
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

    async def upsert_indexed_document(
        self,
        doc_id: str,
        source_id: str,
        file_path: str,
        file_hash: str,
        chunk_count: int,
    ) -> None:
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO indexed_documents
                   (id, data_source_id, file_path, file_hash, chunk_count, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(data_source_id, file_path) DO UPDATE SET
                       file_hash = ?, chunk_count = ?, indexed_at = ?""",
                (doc_id, source_id, file_path, file_hash, chunk_count, now,
                 file_hash, chunk_count, now),
            )
            await db.commit()

    async def delete_indexed_documents(self, source_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM indexed_documents WHERE data_source_id = ?",
                (source_id,),
            )
            await db.commit()

    # ── NexgAI Sessions ──

    async def get_nexgai_session(self, myai_user_id: str) -> str | None:
        """Return the NexgAI session_id for a MyAi user, or None."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT nexgai_session_id FROM nexgai_sessions WHERE myai_user_id = ?",
                (myai_user_id,),
            )
            row = await cursor.fetchone()
            if row:
                # Update last_used_at
                await db.execute(
                    "UPDATE nexgai_sessions SET last_used_at = ? WHERE myai_user_id = ?",
                    (datetime.utcnow().isoformat(), myai_user_id),
                )
                await db.commit()
                return row[0]
            return None

    async def set_nexgai_session(self, myai_user_id: str, nexgai_session_id: str) -> None:
        """Store or update the NexgAI session mapping for a user."""
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO nexgai_sessions (myai_user_id, nexgai_session_id, created_at, last_used_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(myai_user_id) DO UPDATE SET
                       nexgai_session_id = ?, last_used_at = ?""",
                (myai_user_id, nexgai_session_id, now, now, nexgai_session_id, now),
            )
            await db.commit()

    async def delete_nexgai_session(self, myai_user_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM nexgai_sessions WHERE myai_user_id = ?",
                (myai_user_id,),
            )
            await db.commit()

    # ── Feedback (Phase 4) ──

    async def add_feedback(
        self,
        feedback_id: str,
        message_id: int,
        conversation_id: str,
        user_id: str,
        rating: str,
        comment: str = "",
        source: str = "local",
        agent_name: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO feedback
                   (id, message_id, conversation_id, user_id, rating, comment, source, agent_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (feedback_id, message_id, conversation_id, user_id, rating,
                 comment, source, agent_name, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def get_feedback_stats(self, period_hours: int = 24, source: str | None = None) -> dict:
        """Return aggregate feedback stats for the given period."""
        from datetime import timedelta
        cutoff = (datetime.utcnow() - timedelta(hours=period_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            base = "SELECT rating, COUNT(*) FROM feedback WHERE created_at > ?"
            params: list = [cutoff]
            if source:
                base += " AND source = ?"
                params.append(source)
            base += " GROUP BY rating"
            cursor = await db.execute(base, params)
            rows = await cursor.fetchall()

        counts = {"up": 0, "down": 0}
        for rating, cnt in rows:
            counts[rating] = cnt
        total = counts["up"] + counts["down"]
        return {
            "total": total,
            "thumbs_up": counts["up"],
            "thumbs_down": counts["down"],
            "satisfaction_pct": round(counts["up"] / total * 100, 1) if total else 0.0,
        }

    async def get_negative_feedback_since(self, since: str, limit: int = 100) -> list[dict]:
        """Fetch thumbs-down feedback with associated message content."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT f.*, m.content AS message_content,
                          (SELECT m2.content FROM messages m2
                           WHERE m2.conversation_id = f.conversation_id
                             AND m2.id < f.message_id
                           ORDER BY m2.id DESC LIMIT 1) AS user_query
                   FROM feedback f
                   JOIN messages m ON m.id = f.message_id
                   WHERE f.rating = 'down' AND f.created_at > ?
                   ORDER BY f.created_at DESC LIMIT ?""",
                (since, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_positive_feedback_since(self, since: str, limit: int = 50) -> list[dict]:
        """Fetch thumbs-up feedback on local LLM responses."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT f.*, m.content AS message_content,
                          (SELECT m2.content FROM messages m2
                           WHERE m2.conversation_id = f.conversation_id
                             AND m2.id < f.message_id
                           ORDER BY m2.id DESC LIMIT 1) AS user_query
                   FROM feedback f
                   JOIN messages m ON m.id = f.message_id
                   WHERE f.rating = 'up' AND f.source = 'local' AND f.created_at > ?
                   ORDER BY f.created_at DESC LIMIT ?""",
                (since, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Learning Entries (Phase 4) ──

    async def add_learning_entry(self, entry: dict) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO learning_entries
                   (id, entry_type, source, agent_name, trigger_feedback_ids,
                    original_query, original_response, suggested_improvement,
                    status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (entry["id"], entry["entry_type"], entry["source"],
                 entry.get("agent_name"), entry["trigger_feedback_ids"],
                 entry["original_query"], entry["original_response"],
                 entry["suggested_improvement"], datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def get_learning_entries(
        self, status: str | None = None, entry_type: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM learning_entries WHERE 1=1"
            params: list = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if entry_type:
                query += " AND entry_type = ?"
                params.append(entry_type)
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_learning_entry(self, entry_id: str, **kwargs) -> bool:
        if not kwargs:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [entry_id]
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"UPDATE learning_entries SET {set_clause} WHERE id = ?", values
            )
            await db.commit()
            return cursor.rowcount > 0

    # ── Prompt Versions (Phase 4) ──

    async def add_prompt_version(self, version: dict) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            # Deactivate current active
            await db.execute(
                "UPDATE prompt_versions SET is_active = 0 WHERE source = ? AND is_active = 1",
                (version["source"],),
            )
            await db.execute(
                """INSERT INTO prompt_versions
                   (id, source, prompt_text, is_active, learning_entry_id, created_by, created_at)
                   VALUES (?, ?, ?, 1, ?, ?, ?)""",
                (version["id"], version["source"], version["prompt_text"],
                 version.get("learning_entry_id"), version["created_by"],
                 datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def get_active_prompt(self, source: str = "local") -> str | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT prompt_text FROM prompt_versions WHERE source = ? AND is_active = 1",
                (source,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_prompt_versions(self, source: str = "local", limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM prompt_versions WHERE source = ? ORDER BY created_at DESC LIMIT ?",
                (source, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Satisfaction Snapshots (Phase 4) ──

    async def save_satisfaction_snapshot(self, snapshot_date: str, source: str, stats: dict) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO satisfaction_snapshots
                   (snapshot_date, source, total_feedback, thumbs_up, thumbs_down, satisfaction_pct)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(snapshot_date, source) DO UPDATE SET
                       total_feedback = ?, thumbs_up = ?, thumbs_down = ?, satisfaction_pct = ?""",
                (snapshot_date, source,
                 stats["total"], stats["thumbs_up"], stats["thumbs_down"], stats["satisfaction_pct"],
                 stats["total"], stats["thumbs_up"], stats["thumbs_down"], stats["satisfaction_pct"]),
            )
            await db.commit()

    async def get_satisfaction_trend(self, days: int = 30) -> list[dict]:
        from datetime import timedelta
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM satisfaction_snapshots
                   WHERE snapshot_date >= ? ORDER BY snapshot_date""",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_accessible_data_sources(self, role_level: str) -> list[dict]:
        """Return active data sources where *min_role_level* is at or below the given role rank.

        Role rank: super_admin=3, admin=2, manager=1, employee=0.
        A source with min_role_level='manager' (rank 1) is accessible to manager, admin, super_admin.
        """
        rank_map = {"super_admin": 3, "admin": 2, "manager": 1, "employee": 0}
        user_rank = rank_map.get(role_level, 0)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM data_sources WHERE is_active = 1"
            )
            rows = await cursor.fetchall()

        results = []
        for row in rows:
            source_rank = rank_map.get(row["min_role_level"], 0)
            if user_rank >= source_rank:
                results.append(dict(row))
        return results

    # ── Reminder persistence ──

    async def save_reminder(self, id: str, user_id: str, message: str, due_at: str, created_at: str):
        """Insert a new reminder row."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO reminders (id, user_id, message, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (id, user_id, message, due_at, created_at),
            )
            await db.commit()

    async def get_active_reminders(self) -> list[dict]:
        """Return all reminders that have not been fired yet."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM reminders WHERE fired = 0")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def mark_reminder_fired(self, id: str):
        """Mark a reminder as fired."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (id,))
            await db.commit()

    async def get_user_reminders(self, user_id: str) -> list[dict]:
        """Return active (unfired) reminders for a specific user."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM reminders WHERE user_id = ? AND fired = 0", (user_id,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def delete_reminder(self, id: str):
        """Delete a reminder row."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM reminders WHERE id = ?", (id,))
            await db.commit()
