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
            await db.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
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
                CREATE INDEX IF NOT EXISTS idx_meeting_history_user
                ON meeting_history(user_id)
            """)
            await db.commit()

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
                "INSERT INTO conversations (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conv_id, user_id, now, now),
            )
            await db.commit()
            return Conversation(id=conv_id, user_id=user_id)

    async def add_message(self, conversation_id: str, message: Message):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_name, timestamp) VALUES (?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    message.role.value,
                    message.content,
                    message.tool_name,
                    message.timestamp.isoformat(),
                ),
            )
            await db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), conversation_id),
            )
            await db.commit()

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

    async def save_permission(self, user_id: str, directory: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO permissions (user_id, directory, granted_at) VALUES (?, ?, ?)",
                (user_id, directory, datetime.utcnow().isoformat()),
            )
            await db.commit()
