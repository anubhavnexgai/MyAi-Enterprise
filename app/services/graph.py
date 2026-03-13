import base64
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

class GraphClient:
    """Handles authentication and calls to Microsoft Graph API."""
    def __init__(self):
        self.client_id = settings.microsoft_app_id
        self.client_secret = settings.microsoft_app_password
        self.tenant_id = settings.microsoft_app_tenant_id
        self.token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        # Token cache: (token, expiry_timestamp)
        self._graph_token_cache: tuple[str, float] | None = None
        self._bot_token_cache: tuple[str, float] | None = None

    async def get_access_token(self) -> str:
        """Fetch an OAuth2 access token for Microsoft Graph (cached)."""
        if not self.tenant_id:
            raise ValueError("microsoft_app_tenant_id is not set. Cannot authenticate with Microsoft Graph.")

        import time
        if self._graph_token_cache:
            token, expiry = self._graph_token_cache
            if time.time() < expiry - 60:  # refresh 60s before expiry
                return token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                }
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            self._graph_token_cache = (token, time.time() + expires_in)
            return token

    async def answer_call(self, callback_url: str, call_id: str):
        """Answer an incoming Teams P2P or Group call via Graph API."""
        token = await self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # The payload to answer an incoming notification call
        payload = {
            "@odata.type": "#microsoft.graph.call",
            "callbackUri": callback_url,
            "acceptedModalities": ["audio"], # We only need audio/transcript
            "mediaConfig": {
                "@odata.type": "#microsoft.graph.serviceHostedMediaConfig"
            }
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://graph.microsoft.com/v1.0/communications/calls/{call_id}/answer",
                headers=headers,
                json=payload
            )
            resp.raise_for_status()
            logger.info(f"Answered incoming call via Graph: {resp.status_code}")

    async def join_meeting_by_url(self, callback_url: str, join_url: str, thread_id: str) -> dict:
        """Join a specific Teams meeting using its web join URL."""
        token = await self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "@odata.type": "#microsoft.graph.call",
            "callbackUri": callback_url,
            "requestedModalities": ["audio"],
            "mediaConfig": {
                "@odata.type": "#microsoft.graph.serviceHostedMediaConfig"
            },
            "meetingInfo": {
                "@odata.type": "#microsoft.graph.joinWebUrlMeetingInfo",
                "joinWebUrl": join_url
            },
            "tenantId": self.tenant_id
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://graph.microsoft.com/v1.0/communications/calls",
                headers=headers,
                json=payload
            )
            
            if resp.status_code not in (200, 201, 202):
                logger.error(f"Error joining meeting: Status {resp.status_code}, Response: {resp.text}")
                resp.raise_for_status()
                
            logger.info(f"Successfully joined meeting: {resp.json().get('id')}")
            return resp.json()

    async def subscribe_to_transcript(self, meeting_id: str, notification_url: str):
        """Create a webhook subscription to a meeting's live transcript."""
        token = await self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        # Subscriptions expire, so we set it for 1 hour from now
        expiration = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        payload = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": f"communications/onlineMeetings/{meeting_id}/transcripts",
            "expirationDateTime": expiration,
            "clientState": settings.transcript_webhook_secret
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://graph.microsoft.com/v1.0/subscriptions",
                headers=headers,
                json=payload
            )
            resp.raise_for_status()
            logger.info(f"Successfully subscribed to transcript for meeting {meeting_id}")
            return resp.json()

    async def get_call_details(self, call_id: str) -> dict:
        """Fetch full call details from Graph to extract meeting info."""
        token = await self.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://graph.microsoft.com/v1.0/communications/calls/{call_id}",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def resolve_meeting_id_from_join_url(self, join_url: str) -> str | None:
        """Resolve a Teams meeting join URL to an onlineMeeting ID via Graph."""
        token = await self.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/communications/onlineMeetings",
                headers=headers,
                params={"$filter": f"joinWebUrl eq '{join_url}'"},
            )
            if resp.status_code == 200:
                meetings = resp.json().get("value", [])
                if meetings:
                    return meetings[0].get("id")

        logger.warning(f"Could not resolve meeting ID for join URL: {join_url}")
        return None

    async def send_proactive_message(
        self, service_url: str, conversation_id: str, message: str
    ) -> None:
        """Send a proactive message to a Teams conversation using Bot Framework REST API."""
        token = await self._get_bot_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "type": "message",
            "text": message,
        }
        # Bot Framework endpoint for sending to a conversation
        url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code not in (200, 201, 202):
                logger.error(
                    f"Proactive message failed: {resp.status_code} {resp.text}"
                )
            else:
                logger.info(f"Proactive message sent to conversation {conversation_id}")

    async def _get_bot_token(self) -> str:
        """Get a Bot Framework token for proactive messaging (cached)."""
        import time
        if self._bot_token_cache:
            token, expiry = self._bot_token_cache
            if time.time() < expiry - 60:
                return token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://api.botframework.com/.default",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            self._bot_token_cache = (token, time.time() + expires_in)
            return token

    async def fetch_transcript_content(self, meeting_id: str) -> list[dict]:
        """Fetch all available transcript content for a meeting.

        Returns a list of dicts with 'id' and 'content' keys.
        This is used for polling (fallback when webhook notifications are delayed).
        """
        token = await self.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        results = []

        async with httpx.AsyncClient() as client:
            # List transcript resources
            resp = await client.get(
                f"https://graph.microsoft.com/v1.0/communications/onlineMeetings/{meeting_id}/transcripts",
                headers=headers,
            )
            if resp.status_code != 200:
                logger.warning(f"Failed to list transcripts: {resp.status_code}")
                return results

            transcripts = resp.json().get("value", [])
            for t in transcripts:
                t_id = t.get("id")
                if not t_id:
                    continue
                # Fetch content for each transcript resource
                content_resp = await client.get(
                    f"https://graph.microsoft.com/v1.0/communications/onlineMeetings/{meeting_id}/transcripts/{t_id}/content",
                    headers={**headers, "Accept": "text/vtt"},
                )
                if content_resp.status_code == 200:
                    results.append({"id": t_id, "content": content_resp.text})

        return results
