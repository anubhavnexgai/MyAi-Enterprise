"""VisionService — multimodal image understanding via local Ollama.

Uses whatever vision model the policy `models.routes.vision` points at
(default `llava:7b`). Free, local, no third-party API.

Public API:
    VisionService().describe(image_path, prompt="...") -> str
    VisionService().describe_screen(prompt="...")      -> str

Both return plain text descriptions. The Ollama vision model must be pulled
first: `ollama pull llava:7b` (or `qwen2-vl`).
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

from app.config import settings
from app.services.policy import get_policy

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "Describe this image in 2-4 sentences. Focus on what's actionable for a "
    "personal AI assistant — what the user might want to do, any text "
    "visible, any UI state."
)


class VisionService:
    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.timeout = max(60, getattr(settings, "ollama_timeout", 60))

    async def describe(self, image_path: str | Path, prompt: str = "") -> str:
        path = Path(image_path)
        if not path.is_file():
            return f"Image not found: {path}"
        try:
            data = path.read_bytes()
        except Exception as exc:
            return f"Could not read image: {exc}"

        b64 = base64.b64encode(data).decode("ascii")
        model = get_policy().model_for("vision")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt or DEFAULT_PROMPT,
                    "images": [b64],
                }
            ],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 512},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(f"{self.base_url}/api/chat", json=payload)
                r.raise_for_status()
                resp = r.json()
        except httpx.HTTPStatusError as exc:
            # Most likely cause: model not pulled. Surface a useful message.
            body = exc.response.text[:300] if exc.response is not None else ""
            return (f"Vision model '{model}' unavailable. "
                    f"Try `ollama pull {model}`. ({body})")
        except Exception as exc:
            return f"Vision call failed: {exc}"

        return resp.get("message", {}).get("content", "").strip() or "(empty response)"

    async def describe_screen(self, prompt: str = "") -> str:
        """Take a screenshot, describe it. Reuses the existing screenshot helper."""
        from app.services.file_access import FileAccessService  # lazy import
        # Take screenshot to a temp file via pyautogui (already a MyAi dep).
        try:
            import pyautogui  # type: ignore
            import tempfile, os
            fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="myai_screen_")
            os.close(fd)
            pyautogui.screenshot().save(tmp_path)
            try:
                return await self.describe(tmp_path, prompt or DEFAULT_PROMPT)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except ImportError:
            return "pyautogui not installed — can't take screenshot."
        except Exception as exc:
            return f"Screen description failed: {exc}"


_singleton: VisionService | None = None


def get_vision() -> VisionService:
    global _singleton
    if _singleton is None:
        _singleton = VisionService()
    return _singleton
