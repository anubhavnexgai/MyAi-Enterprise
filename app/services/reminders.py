"""Reminder/scheduled task service for MyAi."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class Reminder:
    id: str
    user_id: str
    message: str
    due_at: datetime
    created_at: datetime = field(default_factory=datetime.now)
    fired: bool = False


class ReminderService:
    """In-memory reminder service with background check loop."""

    def __init__(self):
        self._reminders: list[Reminder] = []
        self._counter = 0
        self._notify_callback: Callable[[str, str], Awaitable[None]] | None = None

    def set_notify_callback(self, callback: Callable[[str, str], Awaitable[None]]) -> None:
        """Set the callback function to notify users. Signature: async (user_id, message) -> None"""
        self._notify_callback = callback

    def add_reminder(self, user_id: str, message: str, due_at: datetime) -> Reminder:
        """Add a new reminder."""
        self._counter += 1
        reminder = Reminder(
            id=f"rem-{self._counter}",
            user_id=user_id,
            message=message,
            due_at=due_at,
        )
        self._reminders.append(reminder)
        logger.info(f"Reminder added: '{message}' due at {due_at} for user {user_id}")
        return reminder

    def list_reminders(self, user_id: str) -> list[Reminder]:
        """List active reminders for a user."""
        return [r for r in self._reminders if r.user_id == user_id and not r.fired]

    def cancel_reminder(self, reminder_id: str) -> bool:
        """Cancel a reminder by ID."""
        for r in self._reminders:
            if r.id == reminder_id and not r.fired:
                r.fired = True
                return True
        return False

    def get_due_reminders(self) -> list[Reminder]:
        """Get all reminders that are due and haven't fired yet."""
        now = datetime.now()
        due = []
        for r in self._reminders:
            if not r.fired and r.due_at <= now:
                r.fired = True
                due.append(r)
        return due

    async def check_loop(self) -> None:
        """Background loop that checks for due reminders every 15 seconds."""
        logger.info("Reminder check loop started")
        while True:
            try:
                await asyncio.sleep(15)
                active = [r for r in self._reminders if not r.fired]
                if active:
                    logger.info(f"Reminder check: {len(active)} active reminders, callback={'set' if self._notify_callback else 'NOT SET'}")
                due = self.get_due_reminders()
                if due:
                    logger.info(f"Reminder check: {len(due)} reminders DUE NOW")
                    if self._notify_callback:
                        for r in due:
                            try:
                                logger.info(f"Firing reminder: '{r.message}' for user {r.user_id}")
                                await self._notify_callback(r.user_id, f"**Reminder:** {r.message}")
                            except Exception as e:
                                logger.warning(f"Failed to deliver reminder: {e}")
                    else:
                        logger.error("Reminder due but NO CALLBACK SET!")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Reminder check error: {e}")

    @staticmethod
    def parse_time_expression(text: str) -> datetime | None:
        """Parse natural time expressions into a datetime.

        Supports:
        - 'in 5 minutes', 'in 1 hour', 'in 30 seconds'
        - 'at 3pm', 'at 15:00', 'at 3:30pm'
        - 'tomorrow at 9am'
        """
        import re
        text = text.lower().strip()
        now = datetime.now()

        # "in X minutes/hours/seconds"
        m = re.match(r"in\s+(\d+)\s*(seconds?|mins?|minutes?|hours?|hrs?)", text)
        if m:
            amount = int(m.group(1))
            unit = m.group(2)
            if unit.startswith("sec"):
                return now + timedelta(seconds=amount)
            elif unit.startswith("min"):
                return now + timedelta(minutes=amount)
            elif unit.startswith("h"):
                return now + timedelta(hours=amount)

        # "at 3pm", "at 15:00", "at 3:30pm"
        m = re.match(r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ampm = m.group(3)
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)  # Next day
            return target

        # "tomorrow at Xam/pm"
        m = re.match(r"tomorrow\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ampm = m.group(3)
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            target = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target

        return None
