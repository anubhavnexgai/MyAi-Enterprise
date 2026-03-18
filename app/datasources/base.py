"""Abstract base for all data-source connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class DocumentRecord:
    """A single document fetched from a data source."""

    path: str          # unique identifier / file path
    content: bytes     # raw bytes
    mime_type: str     # e.g. "application/pdf"
    file_hash: str     # SHA-256 hex digest of *content*


class BaseConnector(ABC):
    """Every connector must implement these two async methods."""

    name: str = "base"

    @abstractmethod
    async def test_connection(self, config: dict) -> tuple[bool, str]:
        """Return (True, "ok") on success or (False, "<error description>")."""
        ...

    @abstractmethod
    async def fetch_documents(self, config: dict) -> AsyncIterator[DocumentRecord]:
        """Yield :class:`DocumentRecord` instances from the configured source."""
        ...
