"""FR-3.1 -- Local directory connector."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
from collections.abc import AsyncIterator
from pathlib import Path

from app.datasources.base import BaseConnector, DocumentRecord

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: set[str] = {".pdf", ".docx", ".txt", ".md"}

# Map extensions to MIME types (fallback when mimetypes module misses)
_MIME_OVERRIDES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mime_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[ext]
    guess, _ = mimetypes.guess_type(str(path))
    return guess or "application/octet-stream"


class LocalDirectoryConnector(BaseConnector):
    """Walk a local directory and yield supported files."""

    name = "local_directory"

    async def test_connection(self, config: dict) -> tuple[bool, str]:
        """Check that the configured *directory* exists and is readable."""
        directory = config.get("directory", "")
        if not directory:
            return False, "No 'directory' specified in config"

        p = Path(directory)
        if not p.exists():
            return False, f"Directory does not exist: {directory}"
        if not p.is_dir():
            return False, f"Path is not a directory: {directory}"
        if not os.access(str(p), os.R_OK):
            return False, f"Directory is not readable: {directory}"

        return True, "ok"

    async def fetch_documents(self, config: dict) -> AsyncIterator[DocumentRecord]:
        """Yield one :class:`DocumentRecord` per supported file in *directory*."""
        directory = config.get("directory", "")
        root = Path(directory)
        loop = asyncio.get_event_loop()

        for fpath in root.rglob("*"):
            if not fpath.is_file():
                continue
            if fpath.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            try:
                content: bytes = await loop.run_in_executor(
                    None, fpath.read_bytes
                )
                yield DocumentRecord(
                    path=str(fpath),
                    content=content,
                    mime_type=_mime_for(fpath),
                    file_hash=_sha256(content),
                )
            except Exception as exc:
                logger.warning("Failed to read %s: %s", fpath, exc)
