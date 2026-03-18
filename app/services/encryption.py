"""Fernet encryption for data source configuration secrets."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_KEY_FILE = Path("data/fernet.key")


class ConfigEncryption:
    """Encrypt / decrypt data-source config dicts using Fernet symmetric encryption."""

    def __init__(self) -> None:
        self._fernet = Fernet(self._load_key())

    # ── public API ──

    def encrypt(self, config: dict) -> str:
        """Serialise *config* to JSON, encrypt, and return a UTF-8 token string."""
        plaintext = json.dumps(config).encode("utf-8")
        return self._fernet.encrypt(plaintext).decode("utf-8")

    def decrypt(self, encrypted: str) -> dict:
        """Reverse of :meth:`encrypt` -- returns the original dict."""
        plaintext = self._fernet.decrypt(encrypted.encode("utf-8"))
        return json.loads(plaintext.decode("utf-8"))

    # ── key management (private) ──

    @staticmethod
    def _load_key() -> bytes:
        """Return the Fernet key from env-var or on-disk file (auto-generating if needed)."""
        env_key = os.environ.get("FERNET_KEY")
        if env_key:
            logger.info("Using FERNET_KEY from environment variable")
            return env_key.encode("utf-8")

        if _KEY_FILE.exists():
            logger.info("Loading Fernet key from %s", _KEY_FILE)
            return _KEY_FILE.read_bytes().strip()

        # Auto-generate
        key = Fernet.generate_key()
        _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _KEY_FILE.write_bytes(key)
        logger.info("Generated new Fernet key and saved to %s", _KEY_FILE)
        return key
