"""Microsoft Graph client for employee-facing operations.

Uses OAuth2 delegated (authorization code) flow so MyAi acts on behalf
of the signed-in user. Supports: calendar, email, files, people, presence.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = [
    "User.Read",
    "Calendars.ReadWrite",
    "Mail.ReadWrite",
    "Mail.Send",
    "Files.Read.All",
    "People.Read",
    "Presence.Read",
    "Chat.Read",
]


@dataclass
class UserTokens:
    """Stores OAuth2 tokens for a user."""
    access_token: str
    refresh_token: str
    expires_at: float  # Unix timestamp
    scope: str = ""
    user_email: str = ""


class GraphClient:
    """Microsoft Graph client with per-user delegated auth."""

    def __init__(self):
        self.client_id = settings.graph_client_id
        self.client_secret = settings.graph_client_secret
        self.tenant_id = settings.graph_tenant_id or "common"
        self.redirect_uri = settings.graph_redirect_uri
        self._token_store: dict[str, UserTokens] = {}  # slack_user_id -> tokens

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    # -- OAuth2 Flow --

    def get_auth_url(self, state: str = "") -> str:
        """Generate the Microsoft OAuth2 authorization URL."""
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": " ".join(SCOPES) + " offline_access",
            "state": state,
        }
        return (
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize?"
            + urlencode(params)
        )

    async def exchange_code(self, code: str, slack_user_id: str) -> UserTokens:
        """Exchange an authorization code for access + refresh tokens."""
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
                "scope": " ".join(SCOPES) + " offline_access",
            })
            resp.raise_for_status()
            data = resp.json()

        tokens = UserTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            expires_at=time.time() + data.get("expires_in", 3600),
            scope=data.get("scope", ""),
        )

        # Fetch user email for display
        try:
            me = await self._get(tokens.access_token, "/me", params={"$select": "mail,displayName"})
            tokens.user_email = me.get("mail") or me.get("userPrincipalName", "")
        except Exception:
            pass

        self._token_store[slack_user_id] = tokens
        logger.info(f"Graph tokens stored for Slack user {slack_user_id} ({tokens.user_email})")
        return tokens

    async def _refresh_token(self, slack_user_id: str) -> str:
        """Refresh an expired access token."""
        tokens = self._token_store.get(slack_user_id)
        if not tokens or not tokens.refresh_token:
            raise ValueError("No refresh token available. User must re-authenticate.")

        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": tokens.refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(SCOPES) + " offline_access",
            })
            resp.raise_for_status()
            data = resp.json()

        tokens.access_token = data["access_token"]
        tokens.refresh_token = data.get("refresh_token", tokens.refresh_token)
        tokens.expires_at = time.time() + data.get("expires_in", 3600)
        logger.info(f"Graph token refreshed for {slack_user_id}")
        return tokens.access_token

    async def get_token(self, slack_user_id: str) -> str:
        """Get a valid access token for a user, refreshing if needed."""
        tokens = self._token_store.get(slack_user_id)
        if not tokens:
            raise ValueError("Not connected to Microsoft 365. Use `/connect` to sign in.")

        if time.time() >= tokens.expires_at - 60:
            return await self._refresh_token(slack_user_id)

        return tokens.access_token

    def is_user_connected(self, slack_user_id: str) -> bool:
        return slack_user_id in self._token_store

    def disconnect_user(self, slack_user_id: str) -> None:
        self._token_store.pop(slack_user_id, None)

    def get_user_email(self, slack_user_id: str) -> str:
        tokens = self._token_store.get(slack_user_id)
        return tokens.user_email if tokens else ""

    # -- Graph API Helpers --

    async def _get(self, token: str, path: str, params: dict | None = None) -> dict:
        url = f"{GRAPH_BASE}{path}" if path.startswith("/") else f"{GRAPH_BASE}/{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def _post(self, token: str, path: str, json_data: dict) -> dict:
        url = f"{GRAPH_BASE}{path}" if path.startswith("/") else f"{GRAPH_BASE}/{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=json_data,
                timeout=30,
            )
            resp.raise_for_status()
            if resp.status_code == 204:
                return {}
            return resp.json()

    # -- Calendar Operations --

    async def get_calendar_events(
        self, slack_user_id: str, top: int = 10, days_ahead: int = 7
    ) -> list[dict]:
        """Get upcoming calendar events."""
        token = await self.get_token(slack_user_id)
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days_ahead)

        data = await self._get(token, "/me/calendarView", params={
            "startDateTime": now.isoformat(),
            "endDateTime": end.isoformat(),
            "$top": str(top),
            "$orderby": "start/dateTime",
            "$select": "subject,start,end,location,organizer,attendees,isOnlineMeeting,onlineMeetingUrl",
        })

        events = []
        for e in data.get("value", []):
            events.append({
                "subject": e.get("subject", "(No subject)"),
                "start": e.get("start", {}).get("dateTime", ""),
                "end": e.get("end", {}).get("dateTime", ""),
                "location": e.get("location", {}).get("displayName", ""),
                "organizer": e.get("organizer", {}).get("emailAddress", {}).get("name", ""),
                "is_online": e.get("isOnlineMeeting", False),
                "meeting_url": e.get("onlineMeetingUrl", ""),
                "attendees": [
                    a.get("emailAddress", {}).get("name", "")
                    for a in e.get("attendees", [])[:10]
                ],
            })
        return events

    async def create_event(
        self,
        slack_user_id: str,
        subject: str,
        start_time: str,
        end_time: str,
        attendees: list[str] | None = None,
        body: str = "",
        is_online: bool = True,
    ) -> dict:
        """Create a calendar event."""
        token = await self.get_token(slack_user_id)
        payload: dict[str, Any] = {
            "subject": subject,
            "start": {"dateTime": start_time, "timeZone": "UTC"},
            "end": {"dateTime": end_time, "timeZone": "UTC"},
            "isOnlineMeeting": is_online,
        }
        if body:
            payload["body"] = {"contentType": "text", "content": body}
        if attendees:
            payload["attendees"] = [
                {"emailAddress": {"address": email}, "type": "required"}
                for email in attendees
            ]

        return await self._post(token, "/me/events", payload)

    # -- Email Operations --

    async def get_recent_emails(self, slack_user_id: str, top: int = 10, folder: str = "inbox") -> list[dict]:
        """Get recent emails from a folder."""
        token = await self.get_token(slack_user_id)
        data = await self._get(token, f"/me/mailFolders/{folder}/messages", params={
            "$top": str(top),
            "$orderby": "receivedDateTime desc",
            "$select": "subject,from,receivedDateTime,isRead,bodyPreview,importance",
        })

        emails = []
        for m in data.get("value", []):
            emails.append({
                "id": m.get("id", ""),
                "subject": m.get("subject", "(No subject)"),
                "from": m.get("from", {}).get("emailAddress", {}).get("name", "Unknown"),
                "from_email": m.get("from", {}).get("emailAddress", {}).get("address", ""),
                "received": m.get("receivedDateTime", ""),
                "is_read": m.get("isRead", False),
                "preview": m.get("bodyPreview", "")[:200],
                "importance": m.get("importance", "normal"),
            })
        return emails

    async def send_email(
        self,
        slack_user_id: str,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
    ) -> None:
        """Send an email."""
        token = await self.get_token(slack_user_id)
        message: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "text", "content": body},
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in to
            ],
        }
        if cc:
            message["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc
            ]

        await self._post(token, "/me/sendMail", {"message": message})

    async def get_email_body(self, slack_user_id: str, message_id: str) -> dict:
        """Get full email content."""
        token = await self.get_token(slack_user_id)
        data = await self._get(token, f"/me/messages/{message_id}", params={
            "$select": "subject,from,toRecipients,ccRecipients,body,receivedDateTime",
        })
        return {
            "subject": data.get("subject", ""),
            "from": data.get("from", {}).get("emailAddress", {}).get("name", ""),
            "to": [r.get("emailAddress", {}).get("name", "") for r in data.get("toRecipients", [])],
            "body": data.get("body", {}).get("content", ""),
            "received": data.get("receivedDateTime", ""),
        }

    # -- Files (OneDrive) --

    async def get_recent_files(self, slack_user_id: str, top: int = 10) -> list[dict]:
        """Get recently modified files from OneDrive."""
        token = await self.get_token(slack_user_id)
        data = await self._get(token, "/me/drive/recent", params={"$top": str(top)})

        files = []
        for f in data.get("value", []):
            files.append({
                "name": f.get("name", ""),
                "size": f.get("size", 0),
                "modified": f.get("lastModifiedDateTime", ""),
                "web_url": f.get("webUrl", ""),
                "type": "folder" if "folder" in f else f.get("file", {}).get("mimeType", "file"),
            })
        return files

    async def search_files(self, slack_user_id: str, query: str, top: int = 10) -> list[dict]:
        """Search files in OneDrive/SharePoint."""
        token = await self.get_token(slack_user_id)
        data = await self._get(token, f"/me/drive/root/search(q='{query}')", params={
            "$top": str(top),
            "$select": "name,size,lastModifiedDateTime,webUrl",
        })

        return [
            {
                "name": f.get("name", ""),
                "size": f.get("size", 0),
                "modified": f.get("lastModifiedDateTime", ""),
                "web_url": f.get("webUrl", ""),
            }
            for f in data.get("value", [])
        ]

    # -- People --

    async def get_people(self, slack_user_id: str, query: str = "", top: int = 10) -> list[dict]:
        """Search people in the organization."""
        token = await self.get_token(slack_user_id)
        params: dict[str, str] = {"$top": str(top)}
        if query:
            params["$search"] = f'"{query}"'

        data = await self._get(token, "/me/people", params=params)

        people = []
        for p in data.get("value", []):
            people.append({
                "name": p.get("displayName", ""),
                "email": (p.get("scoredEmailAddresses", [{}]) or [{}])[0].get("address", ""),
                "title": p.get("jobTitle", ""),
                "department": p.get("department", ""),
                "company": p.get("companyName", ""),
            })
        return people

    # -- Presence --

    async def get_my_presence(self, slack_user_id: str) -> dict:
        """Get the user's own presence status."""
        token = await self.get_token(slack_user_id)
        data = await self._get(token, "/me/presence")
        return {
            "availability": data.get("availability", "Unknown"),
            "activity": data.get("activity", "Unknown"),
        }

    # -- Profile --

    async def get_my_profile(self, slack_user_id: str) -> dict:
        """Get the user's Microsoft 365 profile."""
        token = await self.get_token(slack_user_id)
        data = await self._get(token, "/me", params={
            "$select": "displayName,mail,jobTitle,department,officeLocation,mobilePhone",
        })
        return {
            "name": data.get("displayName", ""),
            "email": data.get("mail", ""),
            "title": data.get("jobTitle", ""),
            "department": data.get("department", ""),
            "office": data.get("officeLocation", ""),
            "phone": data.get("mobilePhone", ""),
        }
