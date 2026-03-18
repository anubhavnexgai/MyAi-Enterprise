"""FR-3.4 -- Generic REST API connector."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from functools import reduce

import httpx

from app.datasources.base import BaseConnector, DocumentRecord

logger = logging.getLogger(__name__)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_dot_path(obj: dict | list, dot_path: str):
    """Traverse *obj* using a dot-separated key path (e.g. ``data.items.text``)."""
    try:
        return reduce(lambda o, k: o[int(k)] if isinstance(o, list) else o[k],
                       dot_path.split("."), obj)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _build_auth_headers(auth_type: str, auth_config: dict) -> dict[str, str]:
    """Return HTTP headers for the requested auth scheme."""
    if auth_type == "api_key":
        header_name = auth_config.get("header_name", "X-API-Key")
        return {header_name: auth_config["api_key"]}
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {auth_config['token']}"}
    if auth_type == "oauth2":
        # Expect a pre-fetched token in auth_config
        return {"Authorization": f"Bearer {auth_config['access_token']}"}
    # No auth
    return {}


class RESTAPIConnector(BaseConnector):
    """Call one or more REST endpoints, extract text from each response."""

    name = "rest_api"

    async def test_connection(self, config: dict) -> tuple[bool, str]:
        """GET the first endpoint and check for a successful status."""
        base_url = config.get("base_url", "").rstrip("/")
        endpoints: list[str] = config.get("endpoints", [])
        auth_type = config.get("auth_type", "none")
        auth_config = config.get("auth_config", {})

        if not base_url:
            return False, "No 'base_url' specified in config"
        if not endpoints:
            return False, "No 'endpoints' specified in config"

        headers = _build_auth_headers(auth_type, auth_config)

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                url = f"{base_url}{endpoints[0]}"
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
            return True, "ok"
        except httpx.HTTPStatusError as exc:
            return False, f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        except Exception as exc:
            return False, f"Connection failed: {exc}"

    async def fetch_documents(self, config: dict) -> AsyncIterator[DocumentRecord]:
        """Call each endpoint, extract text using *response_text_field*, yield documents."""
        base_url = config.get("base_url", "").rstrip("/")
        endpoints: list[str] = config.get("endpoints", [])
        auth_type = config.get("auth_type", "none")
        auth_config = config.get("auth_config", {})
        text_field = config.get("response_text_field", "")

        headers = _build_auth_headers(auth_type, auth_config)

        async with httpx.AsyncClient(timeout=60) as client:
            for endpoint in endpoints:
                url = f"{base_url}{endpoint}"
                try:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()

                    body = resp.json()

                    # Extract text using dot-notation field or serialise whole body
                    if text_field:
                        text = _resolve_dot_path(body, text_field)
                        if text is None:
                            text = json.dumps(body, default=str)
                        elif not isinstance(text, str):
                            text = json.dumps(text, default=str)
                    else:
                        text = json.dumps(body, default=str)

                    content = text.encode("utf-8")

                    yield DocumentRecord(
                        path=endpoint,
                        content=content,
                        mime_type="application/json",
                        file_hash=_sha256(content),
                    )
                except Exception as exc:
                    logger.warning("Failed to fetch %s: %s", url, exc)
