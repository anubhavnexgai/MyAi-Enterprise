"""Role-Based Access Control service for MyAi."""

from __future__ import annotations

import logging

import aiosqlite

from app.auth.models import RoleLevel, User

logger = logging.getLogger(__name__)

# Mapping from skill names to permission keys
SKILL_PERMISSION_MAP = {
    "it_support": "skill:it_support",
    "hr_ops": "skill:hr_ops",
    "finance": "skill:finance",
    "legal_compliance": "skill:legal_compliance",
    "executive_assistant": "skill:executive_assistant",
    "recruitment": "skill:recruitment",
    "data_analytics": "skill:data_analytics",
    "project_coordination": "skill:project_coordination",
}


class RBACService:
    """Checks permissions, skill access, and file policies based on user role."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def _get_role_permissions(self, role_level: RoleLevel) -> set[str]:
        """Get all permission keys for a role, including inherited permissions."""
        permissions: set[str] = set()
        async with aiosqlite.connect(self.db_path) as db:
            # Gather permissions from this role and all roles it inherits from
            for role in RoleLevel.hierarchy():
                if role_level.inherits_from(role):
                    continue
                # Skip roles higher than the user's role (they don't inherit down)
            for role in RoleLevel.hierarchy():
                # role_level inherits from roles at its level or below
                if not role_level.inherits_from(role):
                    continue
                cursor = await db.execute(
                    "SELECT permission_key FROM role_permissions WHERE role_level = ?",
                    (role.value,),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    permissions.add(row[0])
        return permissions

    async def check_permission(self, user: User, permission_key: str) -> bool:
        """Check if a user has a specific permission.

        Supports wildcard matching: 'skill:*' matches 'skill:it_support'.
        Role hierarchy: super_admin inherits all, admin inherits manager, manager inherits employee.
        """
        if not user.is_active:
            return False

        permissions = await self._get_role_permissions(user.role_level)

        # Direct match
        if permission_key in permissions:
            return True

        # Wildcard match: e.g. 'skill:*' matches 'skill:it_support'
        parts = permission_key.split(":")
        if len(parts) == 2:
            wildcard = f"{parts[0]}:*"
            if wildcard in permissions:
                return True

        return False

    async def get_allowed_skills(self, user: User) -> list[str]:
        """Return list of skill names the user can access, considering overrides."""
        if not user.is_active:
            return []

        permissions = await self._get_role_permissions(user.role_level)

        # Determine which skills the role grants
        allowed_by_role: set[str] = set()
        has_skill_wildcard = "skill:*" in permissions

        for skill_name, perm_key in SKILL_PERMISSION_MAP.items():
            if has_skill_wildcard or perm_key in permissions:
                allowed_by_role.add(skill_name)

        # Apply per-user overrides
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT skill_name, allowed FROM user_skill_overrides WHERE user_id = ?",
                (user.id,),
            )
            overrides = await cursor.fetchall()

        for override in overrides:
            skill_name = override["skill_name"]
            if override["allowed"]:
                allowed_by_role.add(skill_name)
            else:
                allowed_by_role.discard(skill_name)

        return sorted(allowed_by_role)

    async def get_file_policies(self, user: User) -> list[dict]:
        """Return file access policies for the user's role."""
        if not user.is_active:
            return []

        policies: list[dict] = []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Gather policies from this role and all inherited roles
            for role in RoleLevel.hierarchy():
                if not user.role_level.inherits_from(role):
                    continue
                cursor = await db.execute(
                    "SELECT directory_path, access_type FROM file_access_policies WHERE role_level = ?",
                    (role.value,),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    policies.append({
                        "directory_path": row["directory_path"],
                        "access_type": row["access_type"],
                        "role_level": role.value,
                    })

        return policies

    def is_admin(self, user: User) -> bool:
        """Quick synchronous check if user has admin-level role."""
        return user.is_active and user.role_level in (
            RoleLevel.SUPER_ADMIN, RoleLevel.ADMIN,
        )

    async def can_access_admin(self, user: User) -> bool:
        """Check if user can access admin features."""
        return await self.check_permission(user, "admin:dashboard")

    async def can_access_skill(self, user: User, skill_name: str) -> bool:
        """Check if a user can access a specific skill."""
        allowed = await self.get_allowed_skills(user)
        return skill_name in allowed
