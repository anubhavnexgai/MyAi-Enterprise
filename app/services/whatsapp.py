"""Twilio WhatsApp integration for MyAi."""
from __future__ import annotations

import logging
import os

from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Send and receive WhatsApp messages via Twilio."""

    def __init__(self):
        from dotenv import load_dotenv
        load_dotenv()
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        self.whatsapp_number = os.getenv("TWILIO_WHATSAPP_NUMBER", "")
        self._client: Client | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.whatsapp_number)

    @property
    def client(self) -> Client:
        if not self._client:
            self._client = Client(self.account_sid, self.auth_token)
        return self._client

    async def send_message(self, to: str, body: str) -> dict:
        """Send a WhatsApp message via Twilio."""
        if not self.is_configured:
            return {"success": False, "error": "Twilio not configured"}

        try:
            # Ensure WhatsApp prefix
            if not to.startswith("whatsapp:"):
                to = f"whatsapp:{to}"
            from_number = f"whatsapp:{self.whatsapp_number}"

            message = self.client.messages.create(
                body=body,
                from_=from_number,
                to=to,
            )
            logger.info(f"WhatsApp message sent: {message.sid} to {to}")
            return {"success": True, "sid": message.sid}
        except Exception as e:
            logger.error(f"WhatsApp send failed: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def create_twiml_response(body: str) -> str:
        """Create a TwiML response for incoming WhatsApp messages."""
        resp = MessagingResponse()
        resp.message(body)
        return str(resp)
