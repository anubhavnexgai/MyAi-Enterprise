"""ChannelGateway — unified outbound messaging across WhatsApp / Telegram / etc.

This module is intentionally light. It does NOT replace the existing
`whatsapp.py` (which has its own Twilio flows). It sits *above* per-channel
adapters and provides:

  - A single `gateway.broadcast(user_id, text)` call that fans out to every
    channel the user has linked.
  - A pluggable adapter interface (`Channel`) — Telegram is the first real
    implementation; WhatsApp is a thin wrapper over the existing service.
  - An approval-queue notifier hook so when a tool is queued for approval,
    the user gets a ping on every channel they've linked, with a one-line
    way to ✅ or ❌ from any of them.

Telegram setup (free, recommended):
  1. Talk to @BotFather, /newbot, copy the token.
  2. Send any message to your new bot from your personal Telegram.
  3. Set env vars in `.env`:
        TELEGRAM_BOT_TOKEN=...
        TELEGRAM_CHAT_ID=...   (your numeric chat id; bot will log it on first run)
"""
from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


# ---- adapter interface -----------------------------------------------------


class Channel(ABC):
    name: str = "base"
    enabled: bool = False

    @abstractmethod
    async def send(self, user_id: str, text: str) -> bool: ...

    async def start(self) -> None:
        """Optional: long-running listener (e.g. polling). Default: no-op."""


# ---- Telegram adapter ------------------------------------------------------


@dataclass
class _TelegramConfig:
    token: str
    chat_id: str  # numeric, as a string


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, config: _TelegramConfig | None = None):
        if config is None:
            token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
            self.cfg = _TelegramConfig(token=token, chat_id=chat) if token else None
        else:
            self.cfg = config
        self.enabled = bool(self.cfg and self.cfg.token)
        self._inbound_callback = None
        self._poll_task: asyncio.Task | None = None
        self._last_update_id: int = 0

    async def send(self, user_id: str, text: str) -> bool:
        if not self.enabled or not self.cfg:
            return False
        chat_id = self.cfg.chat_id or user_id
        if not chat_id:
            logger.warning("Telegram send skipped: no chat_id")
            return False
        url = f"https://api.telegram.org/bot{self.cfg.token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(url, json=payload)
                if r.status_code == 200:
                    return True
                logger.warning("Telegram send failed %s: %s", r.status_code, r.text[:200])
                return False
        except Exception as exc:
            logger.warning("Telegram send error: %s", exc)
            return False

    def on_inbound(self, callback) -> None:
        """Register a callback `async (user_id, text) -> str | None`."""
        self._inbound_callback = callback

    async def start(self) -> None:
        if not self.enabled or self._poll_task is not None:
            return
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram polling started")

    async def _poll_loop(self) -> None:
        url = f"https://api.telegram.org/bot{self.cfg.token}/getUpdates"
        while True:
            try:
                async with httpx.AsyncClient(timeout=35) as client:
                    r = await client.get(url, params={
                        "offset": self._last_update_id + 1,
                        "timeout": 30,
                    })
                if r.status_code != 200:
                    await asyncio.sleep(5)
                    continue
                for upd in r.json().get("result", []):
                    self._last_update_id = max(self._last_update_id, upd.get("update_id", 0))
                    await self._handle_update(upd)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Telegram poll error: %s", exc)
                await asyncio.sleep(5)

    async def _handle_update(self, upd: dict) -> None:
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not text:
            return

        # Log chat id once for easy setup
        if not self.cfg.chat_id:
            logger.info("Telegram first-message chat_id=%s — set TELEGRAM_CHAT_ID to this", chat_id)
            self.cfg.chat_id = chat_id

        # Approval shortcut: messages of the form "approve N" / "reject N" / "✅ N" / "❌ N"
        from app.services.approval import get_approval
        approval = get_approval()
        m_approve = _try_parse_decision(text)
        if m_approve is not None:
            decision, action_id, note = m_approve
            ok = (approval.approve(action_id, by="telegram", note=note)
                  if decision else
                  approval.reject(action_id, by="telegram", note=note))
            await self.send(chat_id, f"{'✅' if decision else '❌'} action #{action_id} {('approved' if decision else 'rejected') if ok else 'not pending'}")
            return

        # Otherwise hand to chat callback (if registered)
        if self._inbound_callback:
            try:
                resp = await self._inbound_callback(chat_id, text)
                if resp:
                    await self.send(chat_id, resp)
            except Exception as exc:
                logger.warning("Telegram inbound handler error: %s", exc)


def _try_parse_decision(text: str) -> tuple[bool, int, str] | None:
    """Parse "approve 5", "reject 7 because Y", "✅ 5", "❌ 7" → (True/False, id, note)."""
    import re
    t = text.strip()
    m = re.match(r"^\s*(approve|reject|✅|❌|👍|👎)\s+#?(\d+)\s*(.*)$", t, re.IGNORECASE)
    if not m:
        return None
    word = m.group(1).lower()
    decision = word in ("approve", "✅", "👍")
    return (decision, int(m.group(2)), m.group(3).strip())


# ---- WhatsApp adapter (thin wrapper) ---------------------------------------


class WhatsAppChannel(Channel):
    name = "whatsapp"

    def __init__(self):
        # Re-use the existing service. We only need outbound for the gateway —
        # inbound is already handled by the existing aiohttp routes.
        try:
            from app.services.whatsapp import WhatsAppService  # type: ignore
            self._svc = WhatsAppService()
            self.enabled = getattr(self._svc, "is_configured", True)
        except Exception as exc:
            logger.info("WhatsAppChannel disabled: %s", exc)
            self._svc = None
            self.enabled = False

    async def send(self, user_id: str, text: str) -> bool:
        if not self.enabled or self._svc is None:
            return False
        try:
            # WhatsAppService API varies — try a few common shapes.
            for method in ("send_message", "send", "send_outbound"):
                fn = getattr(self._svc, method, None)
                if callable(fn):
                    res = fn(user_id, text)
                    if asyncio.iscoroutine(res):
                        res = await res
                    return bool(res) if res is not None else True
            logger.warning("WhatsAppChannel: no recognised send method on service")
            return False
        except Exception as exc:
            logger.warning("WhatsApp send failed: %s", exc)
            return False


# ---- Gateway ---------------------------------------------------------------


class ChannelGateway:
    def __init__(self):
        self._channels: dict[str, Channel] = {}

    def register(self, channel: Channel) -> None:
        self._channels[channel.name] = channel
        logger.info("Channel registered: %s (enabled=%s)", channel.name, channel.enabled)

    def get(self, name: str) -> Channel | None:
        return self._channels.get(name)

    def enabled_channels(self) -> list[Channel]:
        return [c for c in self._channels.values() if c.enabled]

    async def broadcast(self, user_id: str, text: str) -> dict[str, bool]:
        """Send `text` to every enabled channel, return per-channel success."""
        results: dict[str, bool] = {}
        for ch in self.enabled_channels():
            results[ch.name] = await ch.send(user_id, text)
        return results

    async def start_all(self) -> None:
        for ch in self._channels.values():
            try:
                await ch.start()
            except Exception as exc:
                logger.warning("Channel %s start failed: %s", ch.name, exc)


_singleton: ChannelGateway | None = None


def get_channel_gateway() -> ChannelGateway:
    global _singleton
    if _singleton is None:
        _singleton = ChannelGateway()
        _singleton.register(TelegramChannel())
        _singleton.register(WhatsAppChannel())
    return _singleton


# ---- Approval-queue notifier hook -----------------------------------------


def install_approval_notifier(user_id: str = "user") -> None:
    """Monkey-patch ApprovalService.queue to broadcast to channels.

    Doing this once at startup (from main.py) means every queued action
    auto-pings the user without each call site needing to remember.
    """
    from app.services.approval import get_approval
    approval = get_approval()
    gateway = get_channel_gateway()

    if getattr(approval, "_notifier_installed", False):
        return

    original_queue = approval.queue

    def queue_with_notify(*args, **kwargs):
        action_id = original_queue(*args, **kwargs)
        tool = kwargs.get("tool") or (args[0] if args else "?")
        reason = kwargs.get("reason", "")
        text = (
            f"🔒 *MyAi wants to run* `{tool}` (action #{action_id}).\n"
            f"Reason: _{reason or 'policy.approval_required'}_\n\n"
            f"Reply `approve {action_id}` or `reject {action_id} <note>` "
            "to decide."
        )
        # Fire-and-forget — don't block the caller.
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(gateway.broadcast(user_id, text))
        except RuntimeError:
            pass  # no loop yet; that's fine
        return action_id

    approval.queue = queue_with_notify  # type: ignore[assignment]
    approval._notifier_installed = True  # type: ignore[attr-defined]
    logger.info("Approval queue → channel-broadcast notifier installed")
