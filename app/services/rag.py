from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Lazy import chromadb to avoid startup cost if not used
_chroma_client = None
_collection = None


def _get_collection():
    global _chroma_client, _collection
    if _collection is None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        persist_dir = settings.chroma_path
        os.makedirs(persist_dir, exist_ok=True)

        _chroma_client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        _collection = _chroma_client.get_or_create_collection(
            name="miai_docs",
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


class RAGService:
    """Local RAG pipeline: chunk -> embed (Ollama) -> store (ChromaDB) -> retrieve."""

    CHUNK_SIZE = 512
    CHUNK_OVERLAP = 64

    def __init__(self, ollama_client):
        self.ollama = ollama_client

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks."""
        words = text.split()
        chunks = []
        for i in range(0, len(words), self.CHUNK_SIZE - self.CHUNK_OVERLAP):
            chunk = " ".join(words[i : i + self.CHUNK_SIZE])
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    async def index_file(
        self,
        file_path: str,
        content: str,
        source_id: str | None = None,
    ) -> int:
        """Index a file's content into ChromaDB. Returns number of chunks.

        When *source_id* is provided it is stored in each chunk's metadata so
        that queries can later be scoped to specific data sources.
        """
        collection = _get_collection()
        chunks = self._chunk_text(content)

        ids = []
        embeddings = []
        documents = []
        metadatas = []

        for i, chunk in enumerate(chunks):
            chunk_id = hashlib.md5(f"{file_path}:{i}".encode()).hexdigest()
            embedding = await self.ollama.generate_embeddings(chunk)

            meta: dict = {"source": file_path, "chunk_index": i}
            if source_id is not None:
                meta["source_id"] = source_id

            ids.append(chunk_id)
            embeddings.append(embedding)
            documents.append(chunk)
            metadatas.append(meta)

        if ids:
            collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )

        logger.info(f"Indexed {len(chunks)} chunks from {file_path}")
        return len(chunks)

    async def index_directory(self, dir_path: str) -> str:
        """Recursively index all supported text files in a directory."""
        p = Path(dir_path)
        if not p.exists() or not p.is_dir():
            return f"Invalid directory: {dir_path}"

        text_extensions = {
            ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
            ".toml", ".csv", ".html", ".css", ".xml", ".sql", ".rs", ".go",
        }

        total_chunks = 0
        total_files = 0

        for fpath in p.rglob("*"):
            if fpath.is_file() and fpath.suffix.lower() in text_extensions:
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    chunks = await self.index_file(str(fpath), content)
                    total_chunks += chunks
                    total_files += 1
                except Exception as e:
                    logger.warning(f"Failed to index {fpath}: {e}")

        return f"Indexed {total_files} files ({total_chunks} chunks) from {dir_path}"

    async def query(
        self,
        question: str,
        n_results: int = 5,
        source_ids: list[str] | None = None,
    ) -> str:
        """Retrieve relevant chunks for a question.

        When *source_ids* is provided, results are filtered to only chunks
        whose ``source_id`` metadata is in the given list.
        """
        collection = _get_collection()

        if collection.count() == 0:
            return "No documents indexed yet. Use `/index <path>` to add documents."

        embedding = await self.ollama.generate_embeddings(question)

        query_kwargs: dict = {
            "query_embeddings": [embedding],
            "n_results": min(n_results, collection.count()),
        }

        if source_ids:
            query_kwargs["where"] = {"source_id": {"$in": source_ids}}

        results = collection.query(**query_kwargs)

        if not results["documents"] or not results["documents"][0]:
            return "No relevant documents found."

        context_parts = ["**Retrieved Context:**\n"]
        for i, (doc, meta) in enumerate(
            zip(results["documents"][0], results["metadatas"][0])
        ):
            source = meta.get("source", "unknown")
            context_parts.append(f"[Source: {source}]\n{doc}\n")

        return "\n".join(context_parts)

    async def delete_source_chunks(self, source_id: str) -> None:
        """Delete all ChromaDB chunks that belong to *source_id*."""
        collection = _get_collection()
        try:
            collection.delete(where={"source_id": source_id})
            logger.info("Deleted all chunks for source_id=%s", source_id)
        except Exception as exc:
            logger.warning(
                "Failed to delete chunks for source_id=%s: %s", source_id, exc
            )
