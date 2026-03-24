"""File Watcher Service -- monitors Downloads, Desktop, Documents for new files.

Uses watchdog to detect new file creation and stores notifications that can be
polled by the WebSocket handler.
"""
from __future__ import annotations

import logging
import os
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

logger = logging.getLogger("miai.file_watcher")

# Files to ignore (temp files, system files)
IGNORED_EXTENSIONS = {".tmp", ".crdownload", ".partial", ".part", ".download"}
IGNORED_FILENAMES = {
    "desktop.ini", "thumbs.db", ".ds_store", "~$",
}
# Prefixes that indicate temp/partial files
IGNORED_PREFIXES = ("~$", ".~", "._")


def _is_ignored(filepath: str) -> bool:
    """Return True if this file should be ignored (temp, system, partial download)."""
    name = os.path.basename(filepath).lower()

    # Exact filename matches
    if name in IGNORED_FILENAMES:
        return True

    # Extension matches
    _, ext = os.path.splitext(name)
    if ext in IGNORED_EXTENSIONS:
        return True

    # Prefix matches (temp Office files, etc.)
    for prefix in IGNORED_PREFIXES:
        if name.startswith(prefix):
            return True

    return False


def _get_file_type(filepath: str) -> str:
    """Return a human-readable file type from extension."""
    ext = os.path.splitext(filepath)[1].lower()
    type_map = {
        ".pdf": "PDF Document",
        ".doc": "Word Document",
        ".docx": "Word Document",
        ".xls": "Excel Spreadsheet",
        ".xlsx": "Excel Spreadsheet",
        ".ppt": "PowerPoint",
        ".pptx": "PowerPoint",
        ".txt": "Text File",
        ".csv": "CSV File",
        ".json": "JSON File",
        ".xml": "XML File",
        ".html": "HTML File",
        ".py": "Python Script",
        ".js": "JavaScript File",
        ".ts": "TypeScript File",
        ".zip": "ZIP Archive",
        ".rar": "RAR Archive",
        ".7z": "7-Zip Archive",
        ".tar": "TAR Archive",
        ".gz": "Gzip Archive",
        ".png": "PNG Image",
        ".jpg": "JPEG Image",
        ".jpeg": "JPEG Image",
        ".gif": "GIF Image",
        ".bmp": "Bitmap Image",
        ".svg": "SVG Image",
        ".webp": "WebP Image",
        ".mp3": "MP3 Audio",
        ".wav": "WAV Audio",
        ".mp4": "MP4 Video",
        ".avi": "AVI Video",
        ".mkv": "MKV Video",
        ".mov": "MOV Video",
        ".exe": "Executable",
        ".msi": "Installer",
        ".iso": "Disk Image",
        ".md": "Markdown File",
        ".log": "Log File",
        ".sql": "SQL File",
        ".yaml": "YAML File",
        ".yml": "YAML File",
    }
    return type_map.get(ext, f"{ext.upper()[1:]} File" if ext else "Unknown")


def _format_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


@dataclass
class FileNotification:
    """A notification about a newly created file."""
    filename: str
    path: str
    size: int
    size_human: str
    file_type: str
    timestamp: float
    folder: str  # e.g. "Downloads", "Desktop", "Documents"

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "path": self.path,
            "size": self.size,
            "size_human": self.size_human,
            "file_type": self.file_type,
            "timestamp": self.timestamp,
            "folder": self.folder,
        }


class _NewFileHandler(FileSystemEventHandler):
    """Watchdog handler that captures file creation events."""

    def __init__(self, folder_label: str, watcher: FileWatcherService):
        super().__init__()
        self.folder_label = folder_label
        self.watcher = watcher

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        filepath = event.src_path
        if _is_ignored(filepath):
            return
        self.watcher._on_file_created(filepath, self.folder_label)


def _resolve_watched_folders() -> dict[str, str]:
    """Detect Downloads, Desktop, Documents paths (handling OneDrive redirects)."""
    home = os.path.expanduser("~")
    folder_map: dict[str, str] = {}

    for name in ("Downloads", "Desktop", "Documents"):
        onedrive_path = os.path.join(home, "OneDrive", name)
        direct_path = os.path.join(home, name)
        if Path(onedrive_path).is_dir():
            folder_map[name] = onedrive_path
        elif Path(direct_path).is_dir():
            folder_map[name] = direct_path
        # skip if neither exists

    return folder_map


class FileWatcherService:
    """Monitors user folders for new files and stores notifications.

    Usage:
        watcher = FileWatcherService()
        watcher.start()
        ...
        notifications = watcher.get_pending_notifications()
        ...
        watcher.stop()
    """

    def __init__(self, debounce_seconds: float = 5.0):
        self._observer: Observer | None = None
        self._notifications: list[FileNotification] = []
        self._lock = threading.Lock()
        self._recent: dict[str, float] = {}  # filepath -> last notification time
        self._debounce_seconds = debounce_seconds
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start watching folders for new files."""
        if self._running:
            logger.warning("File watcher already running")
            return

        folders = _resolve_watched_folders()
        if not folders:
            logger.warning("No watchable folders found (Downloads, Desktop, Documents)")
            return

        self._observer = Observer()

        for label, path in folders.items():
            handler = _NewFileHandler(label, self)
            self._observer.schedule(handler, path, recursive=False)
            logger.info(f"File watcher monitoring: {label} -> {path}")

        self._observer.daemon = True
        self._observer.start()
        self._running = True
        logger.info("File watcher service started")

    def stop(self) -> None:
        """Stop the file watcher."""
        if self._observer and self._running:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            self._running = False
            logger.info("File watcher service stopped")

    def _on_file_created(self, filepath: str, folder_label: str) -> None:
        """Called by the handler when a new file is created."""
        now = time.time()

        # Debounce: skip if we already notified about this file recently
        with self._lock:
            last_time = self._recent.get(filepath, 0.0)
            if now - last_time < self._debounce_seconds:
                return
            self._recent[filepath] = now

        # Wait a moment for the file to finish writing, then check it exists and has size
        # (watchdog fires on creation, file may still be writing)
        try:
            # Small delay to let file settle (e.g. browser downloads create file then write)
            time.sleep(0.5)
            if not os.path.isfile(filepath):
                return
            size = os.path.getsize(filepath)
        except OSError:
            return

        filename = os.path.basename(filepath)
        file_type = _get_file_type(filepath)
        size_human = _format_size(size)

        notification = FileNotification(
            filename=filename,
            path=filepath,
            size=size,
            size_human=size_human,
            file_type=file_type,
            timestamp=now,
            folder=folder_label,
        )

        with self._lock:
            self._notifications.append(notification)

        logger.info(f"New file detected: {filename} ({size_human}) in {folder_label}")

    def get_pending_notifications(self) -> list[FileNotification]:
        """Return and clear all pending file notifications.

        Thread-safe: can be called from asyncio context.
        """
        with self._lock:
            pending = list(self._notifications)
            self._notifications.clear()

            # Also prune old entries from the debounce cache (older than 60s)
            cutoff = time.time() - 60.0
            self._recent = {
                k: v for k, v in self._recent.items() if v > cutoff
            }

        return pending

    def format_notifications_message(self, notifications: list[FileNotification]) -> str:
        """Format a list of notifications into a user-friendly message."""
        if not notifications:
            return ""

        if len(notifications) == 1:
            n = notifications[0]
            return (
                f"New file detected in **{n.folder}**:\n"
                f"- **{n.filename}** ({n.size_human}, {n.file_type})"
            )

        lines = [f"**{len(notifications)} new files detected:**"]
        for n in notifications:
            lines.append(f"- **{n.filename}** in {n.folder} ({n.size_human}, {n.file_type})")
        return "\n".join(lines)
