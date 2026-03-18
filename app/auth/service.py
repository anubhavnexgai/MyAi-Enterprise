"""Authentication service — user management, login, sessions."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

import aiosqlite
import bcrypt

from app.auth.models import RoleLevel, Session, User

logger = logging.getLogger(__name__)

SESSION_EXPIRY_HOURS = 24


class AuthService:
    """Handles user creation, authentication, and session management."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _hash_password(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def _verify_password(self, password: str, password_hash: str) -> bool:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))

    async def create_user(
        self,
        email: str,
        display_name: str,
        password: str,
        role_level: RoleLevel = RoleLevel.EMPLOYEE,
        department: str = "",
    ) -> User:
        """Create a new user account. Raises ValueError if email already exists."""
        user_id = str(uuid.uuid4())
        password_hash = self._hash_password(password)
        now = datetime.utcnow().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            # Check for duplicate email
            cursor = await db.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            )
            if await cursor.fetchone():
                raise ValueError(f"User with email '{email}' already exists")

            await db.execute(
                """INSERT INTO users (id, email, display_name, role_level, department,
                   password_hash, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                (user_id, email, display_name, role_level.value, department,
                 password_hash, now),
            )
            await db.commit()

        logger.info(f"Created user {email} ({role_level.value})")
        return User(
            id=user_id,
            email=email,
            display_name=display_name,
            role_level=role_level,
            department=department,
            is_active=True,
            created_at=now,
        )

    async def authenticate(self, email: str, password: str) -> Session | None:
        """Authenticate user by email/password. Returns a Session or None."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users WHERE email = ? AND is_active = 1", (email,)
            )
            row = await cursor.fetchone()
            if not row:
                return None

            if not self._verify_password(password, row["password_hash"]):
                return None

            # Update last_login_at
            now = datetime.utcnow().isoformat()
            await db.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"])
            )

            # Create session token
            token = str(uuid.uuid4())
            expires_at = (datetime.utcnow() + timedelta(hours=SESSION_EXPIRY_HOURS)).isoformat()

            await db.execute(
                "INSERT INTO api_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, row["id"], now, expires_at),
            )
            await db.commit()

        logger.info(f"User {email} authenticated successfully")
        return Session(token=token, user_id=row["id"], created_at=now, expires_at=expires_at)

    async def validate_session(self, token: str) -> User | None:
        """Validate a session token and return the associated User, or None."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute(
                "SELECT * FROM api_sessions WHERE token = ?", (token,)
            )
            session_row = await cursor.fetchone()
            if not session_row:
                return None

            # Check expiry
            if datetime.utcnow() > datetime.fromisoformat(session_row["expires_at"]):
                await db.execute("DELETE FROM api_sessions WHERE token = ?", (token,))
                await db.commit()
                return None

            # Fetch user
            cursor = await db.execute(
                "SELECT * FROM users WHERE id = ? AND is_active = 1",
                (session_row["user_id"],),
            )
            user_row = await cursor.fetchone()
            if not user_row:
                return None

            return User(
                id=user_row["id"],
                email=user_row["email"],
                display_name=user_row["display_name"],
                role_level=RoleLevel(user_row["role_level"]),
                department=user_row["department"] or "",
                is_active=bool(user_row["is_active"]),
                created_at=user_row["created_at"],
                last_login_at=user_row["last_login_at"],
            )

    async def logout(self, token: str) -> None:
        """Invalidate a session token."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM api_sessions WHERE token = ?", (token,))
            await db.commit()
        logger.info("Session invalidated")

    async def change_role(self, user_id: str, new_role: RoleLevel) -> None:
        """Change a user's role level."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET role_level = ? WHERE id = ?",
                (new_role.value, user_id),
            )
            await db.commit()
        logger.info(f"User {user_id} role changed to {new_role.value}")

    async def deactivate_user(self, user_id: str) -> None:
        """Deactivate a user account and invalidate all their sessions."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
            await db.execute("DELETE FROM api_sessions WHERE user_id = ?", (user_id,))
            await db.commit()
        logger.info(f"User {user_id} deactivated")

    async def get_user(self, user_id: str) -> User | None:
        """Get a user by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return User(
                id=row["id"],
                email=row["email"],
                display_name=row["display_name"],
                role_level=RoleLevel(row["role_level"]),
                department=row["department"] or "",
                is_active=bool(row["is_active"]),
                created_at=row["created_at"],
                last_login_at=row["last_login_at"],
            )

    async def get_user_by_email(self, email: str) -> User | None:
        """Get a user by email."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = await cursor.fetchone()
            if not row:
                return None
            return User(
                id=row["id"],
                email=row["email"],
                display_name=row["display_name"],
                role_level=RoleLevel(row["role_level"]),
                department=row["department"] or "",
                is_active=bool(row["is_active"]),
                created_at=row["created_at"],
                last_login_at=row["last_login_at"],
            )

    async def list_users(self) -> list[User]:
        """List all users."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users ORDER BY created_at")
            rows = await cursor.fetchall()
            return [
                User(
                    id=row["id"],
                    email=row["email"],
                    display_name=row["display_name"],
                    role_level=RoleLevel(row["role_level"]),
                    department=row["department"] or "",
                    is_active=bool(row["is_active"]),
                    created_at=row["created_at"],
                    last_login_at=row["last_login_at"],
                )
                for row in rows
            ]

    async def update_user_role(self, user_id: str, new_role: str) -> bool:
        """Update a user's role level. Returns True if user found and updated."""
        try:
            role = RoleLevel(new_role)
        except ValueError:
            raise ValueError(f"Invalid role level: {new_role}")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE users SET role_level = ? WHERE id = ?",
                (role.value, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def set_user_active(self, user_id: str, active: bool) -> bool:
        """Activate or deactivate a user. Returns True if user found."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE users SET is_active = ? WHERE id = ?",
                (1 if active else 0, user_id),
            )
            if not active:
                await db.execute(
                    "DELETE FROM api_sessions WHERE user_id = ?", (user_id,)
                )
            await db.commit()
            return cursor.rowcount > 0

    async def is_setup_complete(self) -> bool:
        """Check if initial setup is complete (at least one super_admin exists)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM users WHERE role_level = ? AND is_active = 1",
                (RoleLevel.SUPER_ADMIN.value,),
            )
            row = await cursor.fetchone()
            return row[0] > 0

    async def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions. Returns count of removed sessions."""
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM api_sessions WHERE expires_at < ?", (now,)
            )
            await db.commit()
            return cursor.rowcount
