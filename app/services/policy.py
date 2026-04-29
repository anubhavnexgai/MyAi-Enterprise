"""PolicyService — loads `config/policy.yaml`, hot-reloads on edit.

This is MyAi's NemoClaw-equivalent governance plane. It tells the
ToolRegistry which tools need approval, which network hosts are allowed,
which model serves which logical role, and how the audit log behaves.

Singleton pattern: `get_policy()` returns the process-wide instance.
"""
from __future__ import annotations

import fnmatch
import logging
import threading
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "tools": {"approval_required": [], "blocked": [], "critic_review": []},
    "network": {"allowlist": [], "on_unlisted": "warn"},
    "models": {"routes": {"default": "qwen2.5:7b"}},
    "approvals": {"auto_approve_after_seconds": 0, "approver": "user"},
    "audit": {"enabled": True, "level": "full", "max_chars_per_field": 4000},
}


class PolicyService:
    def __init__(self, policy_path: Path | str | None = None):
        if policy_path is None:
            # repo_root/config/policy.yaml
            policy_path = Path(__file__).parent.parent.parent / "config" / "policy.yaml"
        self.path = Path(policy_path)
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._lock = threading.RLock()
        self._observer = None
        self.reload()

    # ---- public API --------------------------------------------------------

    def reload(self) -> None:
        with self._lock:
            try:
                if self.path.is_file():
                    raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                    # shallow-merge over defaults so missing sections are safe
                    merged = dict(_DEFAULTS)
                    for k, v in raw.items():
                        if isinstance(v, dict) and isinstance(merged.get(k), dict):
                            merged[k] = {**merged[k], **v}
                        else:
                            merged[k] = v
                    self._data = merged
                    logger.info("PolicyService: loaded %s", self.path)
                else:
                    logger.warning("PolicyService: %s missing, using defaults", self.path)
                    self._data = dict(_DEFAULTS)
            except Exception as exc:
                logger.error("PolicyService: failed to load %s: %s", self.path, exc)
                # Keep prior data on parse failure — fail safe.

    # tools

    def is_approval_required(self, tool_name: str) -> bool:
        return tool_name in self._data["tools"].get("approval_required", [])

    def is_blocked(self, tool_name: str) -> bool:
        return tool_name in self._data["tools"].get("blocked", [])

    def needs_critic(self, tool_name: str) -> bool:
        return tool_name in self._data["tools"].get("critic_review", [])

    # network

    def network_decision(self, host: str) -> str:
        """Return 'allow' | 'queue' | 'block' | 'warn' for a host."""
        host = (host or "").lower().strip()
        for pat in self._data["network"].get("allowlist", []):
            pat_lower = pat.lower()
            if pat_lower == host or fnmatch.fnmatch(host, pat_lower):
                return "allow"
        return self._data["network"].get("on_unlisted", "warn")

    # models

    def model_for(self, role: str) -> str:
        routes = self._data["models"].get("routes", {})
        return routes.get(role) or routes.get("default") or "qwen2.5:7b"

    # approvals / audit knobs

    @property
    def auto_approve_after_seconds(self) -> int:
        return int(self._data["approvals"].get("auto_approve_after_seconds", 0))

    @property
    def approver(self) -> str:
        return str(self._data["approvals"].get("approver", "user"))

    @property
    def audit_enabled(self) -> bool:
        return bool(self._data["audit"].get("enabled", True))

    @property
    def audit_level(self) -> str:
        return str(self._data["audit"].get("level", "full"))

    @property
    def audit_max_chars(self) -> int:
        return int(self._data["audit"].get("max_chars_per_field", 4000))

    # ---- hot-reload --------------------------------------------------------

    def start_watcher(self) -> bool:
        if self._observer is not None:
            return True
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            logger.warning("PolicyService: watchdog not installed, hot-reload disabled")
            return False

        svc = self

        class _PolicyHandler(FileSystemEventHandler):
            def on_modified(self, event):
                if not event.is_directory and Path(event.src_path) == svc.path:
                    logger.info("PolicyService: %s changed, reloading", svc.path.name)
                    svc.reload()

        observer = Observer()
        observer.schedule(_PolicyHandler(), str(self.path.parent), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer
        logger.info("PolicyService: watching %s for hot-reload", self.path)
        return True


_singleton: PolicyService | None = None


def get_policy() -> PolicyService:
    global _singleton
    if _singleton is None:
        _singleton = PolicyService()
    return _singleton
