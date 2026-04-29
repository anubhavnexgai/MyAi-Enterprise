"""PersonaLoader — composes per-persona system prompts from workspace markdown files.

Workspace layout (under app/workspace/):

    identity.md      — global MyAi identity (default persona)
    soul.md          — global hard rules (applied to every persona)
    user.md          — about Anubhav (always injected)
    tools.md         — tool-usage guidance (always injected)
    heartbeat.md     — read by the heartbeat loop, not the chat loop
    agents/<name>/identity.md    — per-persona identity (overrides global identity)
    agents/<name>/soul.md        — per-persona extra rules (appended to global soul)
    agents/<name>/user.md        — optional persona-specific user notes (appended)

Composition order for `compose(persona)`:

    1. Per-persona identity.md (or global identity.md if persona == "default")
    2. Global soul.md
    3. Per-persona soul.md (if exists)
    4. Global user.md
    5. Per-persona user.md (if exists)
    6. Global tools.md

Cache is invalidated via `invalidate()` — call this from the file watcher when
anything under workspace/ changes. Reads from disk lazily on the next access.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PERSONA = "default"


class PersonaLoader:
    """Reads workspace/*.md files and composes per-persona system prompts."""

    def __init__(self, workspace_root: Path | str | None = None):
        if workspace_root is None:
            # Default to <repo>/app/workspace relative to this file
            workspace_root = Path(__file__).parent.parent / "workspace"
        self.root = Path(workspace_root)
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()
        self._observer = None  # watchdog Observer, set by start_watcher()

    # ---- public API --------------------------------------------------------

    def list_personas(self) -> list[str]:
        """Return all available persona names, including 'default'."""
        personas = [DEFAULT_PERSONA]
        agents_dir = self.root / "agents"
        if agents_dir.is_dir():
            for entry in sorted(agents_dir.iterdir()):
                if entry.is_dir() and (entry / "identity.md").is_file():
                    personas.append(entry.name)
        return personas

    def compose(self, persona: str = DEFAULT_PERSONA) -> str:
        """Return the composed system prompt for the given persona.

        Cached. Call `invalidate()` to force reload from disk.
        """
        with self._lock:
            cached = self._cache.get(persona)
            if cached is not None:
                return cached
            composed = self._compose_uncached(persona)
            self._cache[persona] = composed
            return composed

    def invalidate(self, persona: str | None = None) -> None:
        """Drop cached prompts. Pass `None` to drop all, or a name for one."""
        with self._lock:
            if persona is None:
                self._cache.clear()
            else:
                self._cache.pop(persona, None)

    def heartbeat_text(self, persona: str = DEFAULT_PERSONA) -> str:
        """Return the heartbeat.md content for a persona, or the global one."""
        per_persona = self.root / "agents" / persona / "heartbeat.md"
        if per_persona.is_file():
            return self._read(per_persona)
        return self._read(self.root / "heartbeat.md")

    # ---- internals ---------------------------------------------------------

    def _compose_uncached(self, persona: str) -> str:
        sections: list[str] = []

        # 1. Identity (per-persona overrides global)
        per_identity = self.root / "agents" / persona / "identity.md"
        global_identity = self.root / "identity.md"
        if persona != DEFAULT_PERSONA and per_identity.is_file():
            sections.append(self._read(per_identity))
        elif global_identity.is_file():
            sections.append(self._read(global_identity))
        else:
            logger.warning("PersonaLoader: no identity.md found for persona=%s", persona)

        # 2. Global soul (always)
        sections.append(self._read(self.root / "soul.md"))

        # 3. Per-persona soul (additive)
        per_soul = self.root / "agents" / persona / "soul.md"
        if persona != DEFAULT_PERSONA and per_soul.is_file():
            sections.append(self._read(per_soul))

        # 4. Global user
        sections.append(self._read(self.root / "user.md"))

        # 5. Per-persona user (additive)
        per_user = self.root / "agents" / persona / "user.md"
        if persona != DEFAULT_PERSONA and per_user.is_file():
            sections.append(self._read(per_user))

        # 6. Global tools guidance
        sections.append(self._read(self.root / "tools.md"))

        # Filter empties and join with clear separators
        body = "\n\n---\n\n".join(s for s in sections if s.strip())
        return body

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except Exception as exc:
            logger.warning("PersonaLoader: failed to read %s: %s", path, exc)
            return ""

    # ---- hot-reload --------------------------------------------------------

    def start_watcher(self) -> bool:
        """Start a background thread that invalidates the cache when any
        workspace .md file changes. Returns True if watching, False otherwise.

        Safe to call multiple times — second call is a no-op.
        """
        if self._observer is not None:
            return True
        if not self.root.is_dir():
            logger.warning("PersonaLoader: workspace dir missing, watcher not started: %s", self.root)
            return False
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            logger.warning("PersonaLoader: watchdog not installed, hot-reload disabled")
            return False

        loader = self

        class _WorkspaceHandler(FileSystemEventHandler):
            def _maybe_invalidate(self, event) -> None:
                if event.is_directory:
                    return
                if not str(event.src_path).lower().endswith(".md"):
                    return
                logger.info("PersonaLoader: workspace file changed (%s) — invalidating cache",
                            Path(event.src_path).name)
                loader.invalidate()

            def on_modified(self, event):
                self._maybe_invalidate(event)

            def on_created(self, event):
                self._maybe_invalidate(event)

            def on_deleted(self, event):
                self._maybe_invalidate(event)

            def on_moved(self, event):
                self._maybe_invalidate(event)

        observer = Observer()
        observer.schedule(_WorkspaceHandler(), str(self.root), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        logger.info("PersonaLoader: watching %s for hot-reload", self.root)
        return True

    def stop_watcher(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception as exc:
                logger.warning("PersonaLoader: error stopping watcher: %s", exc)
            self._observer = None


# ---- module-level singleton ------------------------------------------------

_singleton: PersonaLoader | None = None


def get_persona_loader() -> PersonaLoader:
    """Return the process-wide PersonaLoader singleton."""
    global _singleton
    if _singleton is None:
        _singleton = PersonaLoader()
    return _singleton
