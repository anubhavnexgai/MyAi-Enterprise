"""FR-3.3 -- SharePoint / Microsoft Graph connector."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator

import httpx

from app.datasources.base import BaseConnector, DocumentRecord

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _get_app_token(client_id: str, client_secret: str, tenant_id: str) -> str:
    """Obtain an access token using the OAuth2 client_credentials flow."""
    url = TOKEN_URL_TEMPLATE.format(tenant_id=tenant_id)
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, data=payload)
        resp.raise_for_status()
        return resp.json()["access_token"]


class SharePointConnector(BaseConnector):
    """Connect to a SharePoint site via the Microsoft Graph API."""

    name = "sharepoint"

    async def test_connection(self, config: dict) -> tuple[bool, str]:
        """Fetch site info to verify credentials / site_id."""
        try:
            token = await _get_app_token(
                config["client_id"],
                config["client_secret"],
                config["tenant_id"],
            )
            site_id = config["site_id"]
            headers = {"Authorization": f"Bearer {token}"}

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{GRAPH_BASE}/sites/{site_id}",
                    headers=headers,
                )
                resp.raise_for_status()

            return True, "ok"
        except KeyError as exc:
            return False, f"Missing config key: {exc}"
        except httpx.HTTPStatusError as exc:
            return False, f"Graph API error: {exc.response.status_code} {exc.response.text[:200]}"
        except Exception as exc:
            return False, f"Connection failed: {exc}"

    async def fetch_documents(self, config: dict) -> AsyncIterator[DocumentRecord]:
        """List files in the site's default drive and download each."""
        token = await _get_app_token(
            config["client_id"],
            config["client_secret"],
            config["tenant_id"],
        )
        site_id = config["site_id"]
        folder_path = config.get("folder_path", "")
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=60) as client:
            # Resolve the drive items URL
            if folder_path:
                items_url = (
                    f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{folder_path}:/children"
                )
            else:
                items_url = f"{GRAPH_BASE}/sites/{site_id}/drive/root/children"

            # Paginate through items
            while items_url:
                resp = await client.get(items_url, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                for item in data.get("value", []):
                    # Skip folders
                    if "folder" in item:
                        continue

                    name = item.get("name", "")
                    download_url = item.get("@microsoft.graph.downloadUrl")
                    if not download_url:
                        logger.debug("No download URL for %s, skipping", name)
                        continue

                    mime_type = item.get("file", {}).get("mimeType", "application/octet-stream")
                    item_path = f"{folder_path}/{name}" if folder_path else name

                    try:
                        dl_resp = await client.get(download_url)
                        dl_resp.raise_for_status()
                        content = dl_resp.content

                        yield DocumentRecord(
                            path=item_path,
                            content=content,
                            mime_type=mime_type,
                            file_hash=_sha256(content),
                        )
                    except Exception as exc:
                        logger.warning("Failed to download %s: %s", name, exc)

                # Handle pagination
                items_url = data.get("@odata.nextLink")
