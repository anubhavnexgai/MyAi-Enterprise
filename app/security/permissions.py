from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


class AuthService:
    """Validates user identity against the allowlist."""

    def is_user_allowed(self, user_id: str) -> bool:
        allowed = settings.allowed_user_list
        if allowed is None:  # wildcard — all allowed
            return True
        return user_id in allowed


class PermissionManager:
    """Manages per-session permission grants for tools.

    Tier 0: Always allowed (chat, reasoning)
    Tier 1: Needs directory grant (file reads in allowed dirs)
    Tier 2: Per-action approval (web search, file write, RAG index)
    Tier 3: Elevated (code execution — future)
    """

    def __init__(self):
        # session-level grants: user_id -> set of granted permissions
        self._session_grants: dict[str, set[str]] = {}
        # web search toggle per user
        self._search_enabled: dict[str, bool] = {}

    def grant(self, user_id: str, permission: str):
        if user_id not in self._session_grants:
            self._session_grants[user_id] = set()
        self._session_grants[user_id].add(permission)
        logger.info(f"Granted '{permission}' to user {user_id}")

    def has_permission(self, user_id: str, permission: str) -> bool:
        return permission in self._session_grants.get(user_id, set())

    def revoke_all(self, user_id: str):
        self._session_grants.pop(user_id, None)
        self._search_enabled.pop(user_id, None)
        logger.info(f"Revoked all permissions for user {user_id}")

    def set_search_enabled(self, user_id: str, enabled: bool):
        self._search_enabled[user_id] = enabled

    def is_search_enabled(self, user_id: str) -> bool:
        return self._search_enabled.get(user_id, False)


auth_service = AuthService()
permission_manager = PermissionManager()
