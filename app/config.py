from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import yaml


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Slack
    slack_bot_token: str = ""       # xoxb-...
    slack_app_token: str = ""       # xapp-...
    slack_signing_secret: str = ""

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_timeout: int = 120

    # Server (debug HTTP server for simulate script)
    host: str = "0.0.0.0"
    port: int = 8001

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

    # Microsoft Graph (delegated auth for employee scenarios)
    graph_client_id: str = ""       # Azure App Registration client ID
    graph_client_secret: str = ""   # Azure App Registration client secret
    graph_tenant_id: str = ""       # Azure tenant ID (or "common" for multi-tenant)
    graph_redirect_uri: str = "http://localhost:8001/auth/callback"

    # NexgAI Integration
    nexgai_enabled: bool = False
    nexgai_base_url: str = "http://localhost:8000"
    nexgai_tenant_id: str = "default"
    nexgai_service_user: str = ""       # service account email for SSO
    nexgai_service_password: str = ""   # service account password
    nexgai_timeout: int = 30
    nexgai_stream_timeout: int = 120
    nexgai_circuit_breaker_threshold: int = 3
    nexgai_circuit_breaker_cooldown: int = 60
    nexgai_agent_cache_ttl: int = 300   # seconds to cache agent list

    # Self-Learning Loop (Phase 4)
    learning_interval_hours: int = 6
    learning_min_negative_feedback: int = 3  # min thumbs-down before engine generates suggestions

    # Meeting Transcript
    meeting_suggestion_debounce_seconds: int = 15
    meeting_transcript_max_chars: int = 12000
    meeting_suggestion_model: str = ""  # empty = use default ollama_model

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
