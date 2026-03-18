"""FR-3.2 -- SQL database connector (SQLite via aiosqlite)."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator

import aiosqlite

from app.datasources.base import BaseConnector, DocumentRecord

logger = logging.getLogger(__name__)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_query(query: str) -> None:
    """Ensure the query is a read-only SELECT statement."""
    stripped = query.strip().upper()
    if not stripped.startswith("SELECT"):
        raise ValueError(
            "Only SELECT queries are allowed. "
            f"Got: {query[:60]!r}"
        )


class SQLDatabaseConnector(BaseConnector):
    """Execute a read-only SELECT query against an SQLite database."""

    name = "sql_database"

    async def test_connection(self, config: dict) -> tuple[bool, str]:
        """Run ``SELECT 1`` against the configured SQLite database."""
        db_type = config.get("db_type", "sqlite")
        if db_type != "sqlite":
            return False, f"Unsupported db_type: {db_type}. Only 'sqlite' is supported."

        connection_string = config.get("connection_string", "")
        if not connection_string:
            return False, "No 'connection_string' specified in config"

        try:
            async with aiosqlite.connect(connection_string) as db:
                cursor = await db.execute("SELECT 1")
                await cursor.fetchone()
            return True, "ok"
        except Exception as exc:
            return False, f"Connection failed: {exc}"

    async def fetch_documents(self, config: dict) -> AsyncIterator[DocumentRecord]:
        """Run the configured query and yield each row as a document."""
        connection_string = config.get("connection_string", "")
        query = config.get("query", "")

        if not query:
            logger.error("No 'query' specified in SQL database config")
            return

        _validate_query(query)

        try:
            async with aiosqlite.connect(connection_string) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(query)
                rows = await cursor.fetchall()
                col_names = [desc[0] for desc in cursor.description] if cursor.description else []

                for idx, row in enumerate(rows):
                    row_dict = {col: row[col] for col in col_names}
                    content = json.dumps(row_dict, default=str).encode("utf-8")

                    yield DocumentRecord(
                        path=f"sql_row_{idx}",
                        content=content,
                        mime_type="application/json",
                        file_hash=_sha256(content),
                    )
        except Exception as exc:
            logger.error("SQL fetch failed: %s", exc)
            raise
