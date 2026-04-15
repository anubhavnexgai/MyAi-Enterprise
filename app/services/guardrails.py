"""
Security Guardrails Service for MyAi.

Inspired by NVIDIA NemoClaw, this module enforces policy-based guardrails
on every tool call before execution.  It checks action policies, path
restrictions, content filtering, and rate limits, and keeps an audit log
of every decision.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_POLICY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "guardrails_policy.yaml"
)


class GuardrailsService:
    """Policy-based guardrail that checks every tool call before execution."""

    # ── Map tool names to logical action categories ──
    _ACTION_MAP: dict[str, str] = {
        "write_file": "file_write",
        "read_file": "file_read",
        "list_directory": "file_read",
        "search_files": "file_read",
        "open_file": "file_read",
        "pdf_reader": "file_read",
        "csv_reader": "file_read",
        "send_email": "send_email",
        "send_whatsapp": "send_whatsapp",
        "app_launcher": "app_launch",
        "clipboard_read": "clipboard",
        "clipboard_write": "clipboard",
        "system_info": "system_read",
        "screenshot": "system_read",
        "git_status": "system_read",
        "web_search": "browser_navigate",
        "url_summarizer": "browser_navigate",
        "open_url": "browser_navigate",
        "browse_web": "browser_navigate",
        "type_in_app": "computer_use",
        "set_reminder": "reminder",
        "rag_query": "rag",
        "mcp_call": "mcp",
        "orchestrate": "orchestrate",
        "delete_file": "file_delete",
        "remove_file": "file_delete",
        "delete_directory": "file_delete",
        "remove_directory": "file_delete",
    }

    def __init__(self, policy_path: str | None = None):
        self._call_counts: dict[str, list[float]] = {}
        self._audit_log: list[dict[str, Any]] = []
        self._policy = self._load_policy(policy_path or _DEFAULT_POLICY_PATH)

    # ── Public API ──────────────────────────────────────────────

    def check(
        self, tool_name: str, arguments: dict[str, Any], user_id: str = ""
    ) -> tuple[bool, str]:
        """Check if a tool call is allowed.

        Returns ``(allowed, reason)`` where *reason* explains the decision.
        """
        warnings: list[str] = []

        # 0. Catch-all: any tool with destructive keywords is blocked
        destructive_keywords = {"delete", "remove", "erase", "wipe", "destroy", "shred", "format_disk"}
        if any(kw in tool_name.lower() for kw in destructive_keywords):
            if tool_name not in self._ACTION_MAP:
                self._audit(tool_name, arguments, user_id, allowed=False,
                            reason=f"Tool '{tool_name}' blocked (destructive keyword)")
                return False, f"Tool '{tool_name}' is blocked — destructive actions are not allowed."

        # 1. Action policy
        action = self._ACTION_MAP.get(tool_name, tool_name)
        blocked_actions: list[str] = self._policy.get("blocked_actions", [])
        if action in blocked_actions:
            self._audit(tool_name, arguments, user_id, allowed=False,
                        reason=f"Action '{action}' is blocked by policy")
            return False, f"Action '{action}' is blocked by policy."

        # 2. Path restrictions (for tools that operate on files)
        path_arg = arguments.get("path") or arguments.get("directory") or ""
        if path_arg:
            path_ok, path_reason = self._check_path_restriction(
                path_arg, action=action
            )
            if not path_ok:
                self._audit(tool_name, arguments, user_id, allowed=False,
                            reason=path_reason)
                return False, path_reason

        # 3. URL / domain checks
        url_arg = arguments.get("url") or ""
        if not url_arg and action == "browser_navigate":
            url_arg = arguments.get("query", "")
        if url_arg:
            url_ok, url_reason = self._check_url(url_arg)
            if not url_ok:
                self._audit(tool_name, arguments, user_id, allowed=False,
                            reason=url_reason)
                return False, url_reason

        # 4. Content filtering (for writes / emails / messages)
        content_fields = ["content", "body", "text", "message"]
        for field in content_fields:
            if field in arguments and isinstance(arguments[field], str):
                content_warnings = self._check_content(arguments[field])
                warnings.extend(content_warnings)

        # 5. Rate limiting
        rate_ok, rate_reason = self._check_rate_limit(tool_name, action)
        if not rate_ok:
            self._audit(tool_name, arguments, user_id, allowed=False,
                        reason=rate_reason)
            return False, rate_reason

        # ── Allowed ──
        reason = "Allowed"
        if warnings:
            reason = f"Allowed with warnings: {'; '.join(warnings)}"
            for w in warnings:
                logger.warning("Guardrail content warning: %s (tool=%s)", w, tool_name)

        self._audit(tool_name, arguments, user_id, allowed=True, reason=reason)
        return True, reason

    def get_audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent *limit* audit entries."""
        return list(self._audit_log[-limit:])

    # ── Internal checks ─────────────────────────────────────────

    def _check_path_restriction(
        self, path: str, action: str = ""
    ) -> tuple[bool, str]:
        """Check if a file path is allowed for the given action."""
        norm = os.path.normpath(os.path.abspath(path)).lower()
        restrictions = self._policy.get("path_restrictions", {})

        # Blocked paths — no access at all
        for blocked in restrictions.get("blocked", []):
            blocked_norm = os.path.normpath(blocked).lower()
            if norm.startswith(blocked_norm):
                return False, f"Access to '{path}' is blocked (protected system path)."

        # Read-only paths — block writes
        write_actions = {"file_write", "file_delete", "system_modify"}
        if action in write_actions:
            for ro in restrictions.get("read_only", []):
                ro_lower = ro.lower()
                # Match both directory prefixes and filename patterns
                if ro_lower.startswith("."):
                    # Pattern like ".env" or ".git" — check basename or path component
                    basename = os.path.basename(norm)
                    if basename == ro_lower or basename.startswith(ro_lower):
                        return False, f"Path '{path}' is read-only (matches '{ro}')."
                    # Also check if the path goes through a .git directory
                    if f"\\{ro_lower}\\" in norm or f"/{ro_lower}/" in norm:
                        return False, f"Path '{path}' is read-only (inside '{ro}/')."
                else:
                    ro_norm = os.path.normpath(ro).lower()
                    if norm.startswith(ro_norm):
                        return False, f"Path '{path}' is read-only."

        return True, "Path allowed."

    def _check_rate_limit(
        self, tool_name: str, action: str
    ) -> tuple[bool, str]:
        """Check rate limits.  Returns ``(allowed, reason)``."""
        now = time.time()
        window = 60.0  # 1 minute
        limits = self._policy.get("rate_limits", {})

        def _check(key: str, max_calls: int) -> tuple[bool, str]:
            timestamps = self._call_counts.setdefault(key, [])
            # Prune old entries
            self._call_counts[key] = [t for t in timestamps if now - t < window]
            if len(self._call_counts[key]) >= max_calls:
                return False, (
                    f"Rate limit exceeded for '{key}': "
                    f"{max_calls} calls per minute."
                )
            self._call_counts[key].append(now)
            return True, "OK"

        # Global tool-call limit
        global_limit = limits.get("tool_calls_per_minute", 50)
        ok, reason = _check("__global__", global_limit)
        if not ok:
            return ok, reason

        # Per-action limits
        if action == "file_write":
            fw_limit = limits.get("file_writes_per_minute", 10)
            ok, reason = _check("file_write", fw_limit)
            if not ok:
                return ok, reason

        if action in ("send_email", "send_whatsapp"):
            em_limit = limits.get("emails_per_minute", 5)
            ok, reason = _check("email_or_message", em_limit)
            if not ok:
                return ok, reason

        return True, "Within rate limits."

    def _check_content(self, content: str) -> list[str]:
        """Scan content for sensitive data.  Returns a list of warnings."""
        warnings: list[str] = []
        content_lower = content.lower()

        # Keyword warnings from policy
        for keyword in self._policy.get("content_warnings", []):
            if keyword.lower() in content_lower:
                warnings.append(
                    f"Content may contain sensitive data (matched '{keyword}')"
                )

        # SQL injection patterns
        sql_patterns = [
            r"(?i)\b(DROP|DELETE|TRUNCATE|ALTER|EXEC|EXECUTE)\s+(TABLE|DATABASE|PROCEDURE)",
            r"(?i);\s*(DROP|DELETE|INSERT|UPDATE|ALTER)\s+",
            r"(?i)'\s*(OR|AND)\s+'?\d*'?\s*=\s*'?\d*",
            r"(?i)UNION\s+SELECT",
        ]
        for pat in sql_patterns:
            if re.search(pat, content):
                warnings.append("Content contains a potential SQL injection pattern")
                break

        return warnings

    def _check_url(self, url: str) -> tuple[bool, str]:
        """Block known malicious / phishing domains."""
        url_lower = url.lower()
        malicious = self._policy.get("malicious_domains", [])
        for domain in malicious:
            if domain.lower() in url_lower:
                return False, f"URL blocked: domain '{domain}' is on the blocklist."
        return True, "URL allowed."

    # ── Helpers ──────────────────────────────────────────────────

    def _audit(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        allowed: bool,
        reason: str,
    ) -> None:
        entry = {
            "timestamp": time.time(),
            "tool_name": tool_name,
            "arguments": _sanitize_args(arguments),
            "user_id": user_id,
            "allowed": allowed,
            "reason": reason,
        }
        self._audit_log.append(entry)
        # Keep the in-memory log bounded
        if len(self._audit_log) > 5000:
            self._audit_log = self._audit_log[-2500:]

        level = logging.INFO if allowed else logging.WARNING
        logger.log(
            level,
            "Guardrail %s tool=%s user=%s reason=%s",
            "ALLOWED" if allowed else "BLOCKED",
            tool_name,
            user_id or "(anonymous)",
            reason,
        )

    @staticmethod
    def _load_policy(path: str) -> dict[str, Any]:
        """Load the YAML policy file.  Falls back to sensible defaults."""
        try:
            resolved = os.path.normpath(path)
            with open(resolved, "r", encoding="utf-8") as f:
                policy = yaml.safe_load(f) or {}
            logger.info("Guardrails policy loaded from %s", resolved)
            return policy
        except FileNotFoundError:
            logger.warning(
                "Guardrails policy not found at %s — using built-in defaults.", path
            )
            return {
                "blocked_actions": ["file_delete", "shell_exec", "system_modify"],
                "path_restrictions": {
                    "blocked": ["C:\\Windows", "C:\\Program Files"],
                    "read_only": [".env", ".git"],
                },
                "rate_limits": {
                    "tool_calls_per_minute": 50,
                    "file_writes_per_minute": 10,
                    "emails_per_minute": 5,
                },
                "content_warnings": ["password", "secret", "api_key", "token"],
                "malicious_domains": [],
            }
        except Exception as exc:
            logger.error("Failed to load guardrails policy: %s", exc)
            return {}


def _sanitize_args(args: dict[str, Any], max_len: int = 200) -> dict[str, Any]:
    """Truncate long argument values for safe audit logging."""
    sanitized: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > max_len:
            sanitized[k] = v[:max_len] + "...[truncated]"
        else:
            sanitized[k] = v
    return sanitized
