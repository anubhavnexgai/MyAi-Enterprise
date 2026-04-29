"""HeartbeatService — periodic proactive agent ticks per persona.

Each registered persona gets its own asyncio task that fires every N seconds.
On each tick:
  1. Read the persona's `heartbeat.md`.
  2. Send a synthesized prompt to the agent: "[HEARTBEAT] check your tasks
     and act if needed".
  3. If the agent returns exactly `HEARTBEAT_OK`, stay silent.
  4. Otherwise broadcast the response via the channel gateway.

Suppresses repeated identical messages to avoid spam: if the heartbeat
returns the same text as last tick, skip sending it.

Disabled by default — start by calling `HeartbeatService(agent).start(...)`
from `main.py` only when you want it running.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.agent.persona import get_persona_loader

if TYPE_CHECKING:
    from app.agent.core import AgentCore

logger = logging.getLogger(__name__)

OK_TOKEN = "HEARTBEAT_OK"


class HeartbeatService:
    def __init__(self, agent: AgentCore):
        self.agent = agent
        self._tasks: dict[str, asyncio.Task] = {}
        self._last_msg: dict[str, str] = {}

    async def start_all(
        self,
        personas: list[str] | None = None,
        interval_seconds: int = 1800,  # 30 min default
        first_tick_delay: int = 60,
    ) -> None:
        """Start a heartbeat loop for each persona that has a heartbeat.md."""
        loader = get_persona_loader()
        if personas is None:
            personas = loader.list_personas()

        for p in personas:
            text = loader.heartbeat_text(p)
            if not text.strip():
                logger.info("Heartbeat: skipping persona '%s' (no heartbeat.md)", p)
                continue
            if p in self._tasks and not self._tasks[p].done():
                continue
            task = asyncio.create_task(self._loop(p, interval_seconds, first_tick_delay))
            self._tasks[p] = task
            logger.info("Heartbeat started for '%s' every %ss", p, interval_seconds)

    def stop_all(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()

    async def _loop(self, persona: str, interval: int, initial_delay: int) -> None:
        try:
            await asyncio.sleep(initial_delay)
            while True:
                try:
                    await self._tick(persona)
                except Exception as exc:
                    logger.warning("Heartbeat tick failed (persona=%s): %s", persona, exc)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    async def _tick(self, persona: str) -> None:
        loader = get_persona_loader()
        hb_text = loader.heartbeat_text(persona)
        if not hb_text.strip():
            return

        # Build a synthesized prompt. The leading @persona switches the
        # agent into that persona for the turn (handled by AgentCore).
        prefix = f"@{persona} " if persona != "default" else ""
        user_text = (
            f"{prefix}[HEARTBEAT] Run your heartbeat checklist now. "
            "If nothing needs doing, reply EXACTLY: HEARTBEAT_OK\n"
            "Otherwise act via tools and end with a one-line user-facing summary.\n\n"
            f"--- heartbeat.md ---\n{hb_text}\n--- end ---"
        )

        try:
            result = await self.agent.process_message(
                user_id="heartbeat",
                user_text=user_text,
                user_name="Heartbeat",
            )
        except Exception as exc:
            logger.warning("Heartbeat process_message failed: %s", exc)
            return

        response = (result.get("text") or "").strip()
        if not response or OK_TOKEN in response.upper():
            logger.debug("Heartbeat (%s): OK", persona)
            return

        # Suppress duplicate consecutive heartbeat messages
        if self._last_msg.get(persona) == response:
            logger.debug("Heartbeat (%s): suppressed duplicate", persona)
            return
        self._last_msg[persona] = response

        # Broadcast via channels
        try:
            from app.services.channels import get_channel_gateway
            gateway = get_channel_gateway()
            text_for_channels = f"💓 _heartbeat ({persona})_\n\n{response[:1200]}"
            await gateway.broadcast(user_id="user", text=text_for_channels)
        except Exception as exc:
            logger.warning("Heartbeat broadcast failed: %s", exc)
