from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

from app.services.file_access import FileAccessService, FileAccessError, PermissionDeniedError
from app.services.web_search import WebSearchService
from app.services.rag import RAGService

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of available tools for the agent."""

    def __init__(
        self,
        file_service: FileAccessService,
        search_service: WebSearchService,
        rag_service: RAGService,
    ):
        self.file_service = file_service
        self.search_service = search_service
        self.rag_service = rag_service

        self._tools: dict[str, Callable[..., Awaitable[str]]] = {
            "read_file": self._read_file,
            "list_directory": self._list_directory,
            "search_files": self._search_files,
            "write_file": self._write_file,
            "web_search": self._web_search,
            "rag_query": self._rag_query,
            "send_email": self._send_email,
            "send_whatsapp": self._send_whatsapp,
            "set_reminder": self._set_reminder,
        }

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name with given arguments."""
        if tool_name not in self._tools:
            return f"Unknown tool: {tool_name}. Available: {', '.join(self._tools.keys())}"

        try:
            result = await self._tools[tool_name](**arguments)
            return result
        except PermissionDeniedError as e:
            return f"⛔ Permission denied: {e}"
        except FileAccessError as e:
            return f"❌ File error: {e}"
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            return f"❌ Tool error: {e}"

    # ── Tool implementations ──

    async def _read_file(self, path: str) -> str:
        content = await self.file_service.read_file(path)
        # Truncate very long files for the LLM context
        if len(content) > 8000:
            return content[:8000] + f"\n\n... [truncated, {len(content)} chars total]"
        return content

    async def _list_directory(self, path: str) -> str:
        return await self.file_service.list_directory(path)

    async def _search_files(self, directory: str, pattern: str) -> str:
        return await self.file_service.search_files(directory, pattern)

    async def _write_file(self, path: str, content: str) -> str:
        return await self.file_service.write_file(path, content)

    async def _web_search(self, query: str) -> str:
        return await self.search_service.search(query)

    async def _rag_query(self, question: str) -> str:
        return await self.rag_service.query(question)

    async def _send_email(self, to: str, subject: str, body: str) -> str:
        """Create .eml draft and open in Outlook."""
        import tempfile
        import os

        # Write .eml manually to avoid MIME line wrapping
        eml_content = (
            f"To: {to}\r\n"
            f"Subject: {subject}\r\n"
            f"X-Unsent: 1\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n"
            f"\r\n"
            f"{body}"
        )

        eml_path = os.path.join(tempfile.gettempdir(), "myai_draft.eml")
        with open(eml_path, "w", encoding="utf-8") as f:
            f.write(eml_content)

        try:
            os.startfile(eml_path)
            return (
                f"Email draft opened in Outlook.\n"
                f"To: {to}\n"
                f"Subject: {subject}\n\n"
                f"Review and click Send."
            )
        except Exception as e:
            return f"Failed to open email: {e}"

    _reminder_service = None  # Set by main.py
    _reminder_user_id = None  # Set per-request

    async def _set_reminder(self, time: str, message: str) -> str:
        """Set a reminder using the reminder service."""
        if not self._reminder_service:
            return "Reminder service is not available."

        from app.services.reminders import ReminderService
        due_at = ReminderService.parse_time_expression(time)
        if not due_at:
            return f"Couldn't understand the time: '{time}'. Try 'in 5 minutes', 'at 3pm', or 'tomorrow at 9am'."

        user_id = self._reminder_user_id or "default"
        reminder = self._reminder_service.add_reminder(user_id, message, due_at)
        return (
            f"Reminder set!\n"
            f"Message: {message}\n"
            f"Due: {due_at.strftime('%I:%M %p, %B %d')}"
        )

    async def _send_whatsapp(self, phone: str, message: str) -> str:
        """Open WhatsApp Web with a pre-filled message."""
        import subprocess
        from urllib.parse import quote

        # Clean phone number — remove spaces, dashes, plus
        clean_phone = phone.replace(" ", "").replace("-", "").replace("+", "")

        # Use wa.me URL which opens WhatsApp Web or desktop app
        wa_url = f"https://wa.me/{clean_phone}?text={quote(message)}"

        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", wa_url],
                creationflags=0x08000000,
            )
            return (
                f"WhatsApp message drafted.\n"
                f"To: {phone}\n"
                f"Message: {message}\n\n"
                f"WhatsApp opened — just click Send."
            )
        except Exception as e:
            return f"Failed to open WhatsApp: {e}"

    @staticmethod
    def parse_tool_call(text: str) -> dict | None:
        """Extract a tool call JSON from the model's response."""
        import re

        # 1. Look for ```tool ... ``` blocks
        pattern = r"```tool\s*\n?\s*(\{.*?\})\s*\n?\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                return ToolRegistry._normalize_tool_call(parsed)
            except json.JSONDecodeError:
                pass

        # 2. Look for ```json ... ``` or ``` ... ``` blocks containing tool calls
        pattern_code = r"```(?:json)?\s*\n?\s*(\{.*?\})\s*\n?\s*```"
        for m in re.finditer(pattern_code, text, re.DOTALL):
            try:
                parsed = json.loads(m.group(1))
                if "name" in parsed and ("arguments" in parsed or "parameters" in parsed):
                    return ToolRegistry._normalize_tool_call(parsed)
            except json.JSONDecodeError:
                continue

        # 3. Try bare JSON with "name" key
        pattern3 = r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"(?:arguments|parameters)"\s*:\s*\{.*?\}\s*\}'
        match3 = re.search(pattern3, text, re.DOTALL)
        if match3:
            try:
                parsed = json.loads(match3.group(0))
                return ToolRegistry._normalize_tool_call(parsed)
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _normalize_tool_call(parsed: dict) -> dict:
        """Normalize tool call dict — handle 'parameters' vs 'arguments' key."""
        if "parameters" in parsed and "arguments" not in parsed:
            parsed["arguments"] = parsed.pop("parameters")
        return parsed
