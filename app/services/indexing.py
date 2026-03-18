"""Background indexing orchestrator for data sources."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from app.datasources.base import BaseConnector
from app.datasources.local_directory import LocalDirectoryConnector
from app.datasources.rest_api import RESTAPIConnector
from app.datasources.sharepoint import SharePointConnector
from app.datasources.sql_database import SQLDatabaseConnector
from app.services.doc_processor import DocumentProcessor
from app.services.encryption import ConfigEncryption
from app.services.rag import RAGService
from app.storage.database import Database

logger = logging.getLogger(__name__)

# Registry: source_type string -> connector class
_CONNECTOR_MAP: dict[str, type[BaseConnector]] = {
    "local_directory": LocalDirectoryConnector,
    "sql_database": SQLDatabaseConnector,
    "sharepoint": SharePointConnector,
    "rest_api": RESTAPIConnector,
}

# Target chunk size (chars) and overlap
_CHUNK_SIZE = 500
_CHUNK_OVERLAP = 50


def _chunk_text(text: str) -> list[str]:
    """Split *text* into ~500-char chunks with overlap."""
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + _CHUNK_SIZE
        chunks.append(text[start:end])
        start = end - _CHUNK_OVERLAP
    return [c for c in chunks if c.strip()]


class IndexingService:
    """Manage background indexing tasks for data sources."""

    def __init__(
        self,
        database: Database,
        rag_service: RAGService,
        doc_processor: DocumentProcessor,
        encryption: ConfigEncryption,
    ) -> None:
        self.database = database
        self.rag_service = rag_service
        self.doc_processor = doc_processor
        self.encryption = encryption
        self._tasks: dict[str, asyncio.Task] = {}

    # ── public API ──

    async def start_indexing(self, source_id: str) -> None:
        """Kick off a background indexing task for *source_id*."""
        # Cancel existing task for this source if running
        self.cancel_indexing(source_id)

        task = asyncio.create_task(self._index_source(source_id))
        self._tasks[source_id] = task
        logger.info("Started indexing task for source %s", source_id)

    async def get_status(self, source_id: str) -> dict:
        """Return the indexing status dict for *source_id*."""
        source = await self.database.get_data_source(source_id)
        if not source:
            return {"status": "not_found"}

        task = self._tasks.get(source_id)
        running = task is not None and not task.done()

        return {
            "source_id": source_id,
            "index_status": source["index_status"],
            "index_error": source.get("index_error"),
            "document_count": source.get("document_count", 0),
            "last_indexed_at": source.get("last_indexed_at"),
            "running": running,
        }

    def cancel_indexing(self, source_id: str) -> None:
        """Cancel a running indexing task, if any."""
        task = self._tasks.pop(source_id, None)
        if task and not task.done():
            task.cancel()
            logger.info("Cancelled indexing task for source %s", source_id)

    # ── internal ──

    async def _index_source(self, source_id: str) -> None:  # noqa: C901
        """Full indexing pipeline for a single data source."""
        try:
            # 1. Load source config from DB
            source = await self.database.get_data_source(source_id)
            if not source:
                logger.error("Data source %s not found", source_id)
                return

            await self.database.update_indexing_status(source_id, "indexing")

            config = self.encryption.decrypt(source["config_encrypted"])
            source_type = source["source_type"]

            # 2. Instantiate the right connector
            connector_cls = _CONNECTOR_MAP.get(source_type)
            if not connector_cls:
                raise ValueError(f"Unknown source_type: {source_type}")
            connector = connector_cls()

            # 3. Delete old chunks for this source before re-indexing
            await self.rag_service.delete_source_chunks(source_id)

            # 4. Iterate documents
            doc_count = 0
            async for doc_record in connector.fetch_documents(config):
                # Check cancellation
                if asyncio.current_task().cancelled():
                    raise asyncio.CancelledError()

                # 4a. Check file_hash -- skip if unchanged
                existing = await self.database.get_indexed_document(
                    source_id, doc_record.path
                )
                if existing and existing["file_hash"] == doc_record.file_hash:
                    logger.debug("Skipping unchanged document: %s", doc_record.path)
                    doc_count += 1
                    continue

                # 4b. Extract text
                text = await self.doc_processor.extract_text(
                    doc_record.content, doc_record.mime_type, doc_record.path
                )
                if not text.strip():
                    logger.debug("Empty text for %s, skipping", doc_record.path)
                    continue

                # 4c. Chunk
                chunks = _chunk_text(text)

                # 4d. Index via RAG service
                chunk_count = await self.rag_service.index_file(
                    file_path=doc_record.path,
                    content=text,
                    source_id=source_id,
                )

                # 4e. Upsert into indexed_documents
                doc_id = str(uuid.uuid4())
                await self.database.upsert_indexed_document(
                    doc_id=doc_id,
                    source_id=source_id,
                    file_path=doc_record.path,
                    file_hash=doc_record.file_hash,
                    chunk_count=chunk_count,
                )
                doc_count += 1

            # 5. Update source status
            now = datetime.utcnow().isoformat()
            await self.database.update_indexing_status(
                source_id,
                status="ready",
                document_count=doc_count,
                last_indexed_at=now,
            )
            logger.info(
                "Indexing complete for source %s: %d documents", source_id, doc_count
            )

        except asyncio.CancelledError:
            logger.info("Indexing cancelled for source %s", source_id)
            await self.database.update_indexing_status(
                source_id, status="cancelled"
            )
        except Exception as exc:
            logger.error("Indexing failed for source %s: %s", source_id, exc)
            await self.database.update_indexing_status(
                source_id, status="error", error=str(exc)
            )
        finally:
            self._tasks.pop(source_id, None)
