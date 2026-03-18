"""Auth data models for MyAi RBAC system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class RoleLevel(str, Enum):
    """Role levels in descending privilege order."""
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"

    @classmethod
    def hierarchy(cls) -> list[RoleLevel]:
        """Return roles in order of privilege, highest first."""
        return [cls.SUPER_ADMIN, cls.ADMIN, cls.MANAGER, cls.EMPLOYEE]

    def rank(self) -> int:
        """Return numeric rank (higher = more privileged). 3=super_admin, 0=employee."""
        hierarchy = RoleLevel.hierarchy()
        return len(hierarchy) - 1 - hierarchy.index(self)

    def inherits_from(self, other: RoleLevel) -> bool:
        """Return True if this role inherits permissions from the other role.

        super_admin inherits all. admin inherits manager. manager inherits employee.
        """
        return self.rank() >= other.rank()


@dataclass
class User:
    """Authenticated user."""
    id: str
    email: str
    display_name: str
    role_level: RoleLevel
    department: str = ""
    is_active: bool = True
    created_at: str = ""
    last_login_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "role_level": self.role_level.value,
            "department": self.department,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
        }


@dataclass
class Session:
    """API session token."""
    token: str
    user_id: str
    created_at: str
    expires_at: str

    def is_expired(self) -> bool:
        return datetime.utcnow() > datetime.fromisoformat(self.expires_at)
