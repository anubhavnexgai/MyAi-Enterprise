from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import yaml


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Azure Bot
    microsoft_app_id: str = ""
    microsoft_app_password: str = ""
    microsoft_app_tenant_id: str = ""

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_timeout: int = 120

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Security
    allowed_users: str = "*"

    # Search
    search_provider: str = "duckduckgo"
    tavily_api_key: str = ""

    # Gemini
    gemini_api_key: str = ""

    # Storage
    database_path: str = "data/miai.db"
    chroma_path: str = "data/chroma"

    # Meeting Transcript
    meeting_suggestion_debounce_seconds: int = 15
    meeting_transcript_max_chars: int = 12000
    meeting_suggestion_model: str = ""  # empty = use default ollama_model
    callback_host: str = ""  # e.g. https://xxxx.ngrok-free.app (for calling/transcript webhooks)
    transcript_webhook_secret: str = "miai-transcript-secret"  # clientState for Graph subscription validation

    @property
    def allowed_user_list(self) -> list[str] | None:
        if self.allowed_users.strip() == "*":
            return None  # all allowed
        return [u.strip() for u in self.allowed_users.split(",") if u.strip()]


class PermissionsConfig:
    """Loads directory allowlist from config/permissions.yaml."""

    def __init__(self, path: str = "config/permissions.yaml"):
        self.allowed_dirs: list[str] = []
        self.path = Path(path)
        self.reload()

    def reload(self):
        if self.path.exists():
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
            self.allowed_dirs = [
                str(Path(d).resolve()) for d in data.get("allowed_directories", [])
            ]

    def is_path_allowed(self, target: str) -> bool:
        resolved = str(Path(target).resolve())
        return any(resolved.startswith(d) for d in self.allowed_dirs)

    def grant_directory(self, directory: str):
        resolved = str(Path(directory).resolve())
        if resolved not in self.allowed_dirs:
            self.allowed_dirs.append(resolved)

    def revoke_all(self):
        self.allowed_dirs.clear()


settings = Settings()
permissions_config = PermissionsConfig()
