"""Document text extraction -- PDF, DOCX, TXT, Markdown."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# MIME-type dispatch table
_TEXT_MIMES = {"text/plain", "text/markdown"}
_PDF_MIMES = {"application/pdf"}
_DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _extract_pdf(content: bytes) -> str:
    """Extract text from a PDF using the *unstructured* library."""
    import tempfile, os
    from unstructured.partition.pdf import partition_pdf

    # unstructured expects a file on disk
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        elements = partition_pdf(filename=tmp_path)
        return "\n\n".join(str(el) for el in elements)
    finally:
        os.unlink(tmp_path)


def _extract_docx(content: bytes) -> str:
    """Extract text from a DOCX using *python-docx*."""
    import io
    from docx import Document

    doc = Document(io.BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _extract_text(content: bytes) -> str:
    """Decode raw bytes as UTF-8 text."""
    return content.decode("utf-8", errors="replace")


class DocumentProcessor:
    """Extract human-readable text from binary document content."""

    async def extract_text(
        self, content: bytes, mime_type: str, file_path: str
    ) -> str:
        """Return extracted text.  CPU-bound work runs in a thread executor."""
        loop = asyncio.get_event_loop()

        if mime_type in _PDF_MIMES or file_path.lower().endswith(".pdf"):
            logger.debug("Extracting PDF: %s", file_path)
            return await loop.run_in_executor(None, _extract_pdf, content)

        if mime_type in _DOCX_MIMES or file_path.lower().endswith(".docx"):
            logger.debug("Extracting DOCX: %s", file_path)
            return await loop.run_in_executor(None, _extract_docx, content)

        if mime_type in _TEXT_MIMES or file_path.lower().endswith((".txt", ".md")):
            logger.debug("Extracting plain text: %s", file_path)
            return await loop.run_in_executor(None, _extract_text, content)

        # Fallback -- attempt UTF-8 decode
        logger.warning(
            "Unknown MIME %s for %s -- attempting UTF-8 decode", mime_type, file_path
        )
        return await loop.run_in_executor(None, _extract_text, content)
