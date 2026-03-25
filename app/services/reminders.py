"""Reminder/scheduled task service for MyAi."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.storage.database import Database

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
    """Persistent reminder service backed by SQLite with in-memory cache for fast polling."""

    def __init__(self, database: Database | None = None):
        self._reminders: list[Reminder] = []
        self._counter = 0
        self._notify_callback: Callable[[str, str], Awaitable[None]] | None = None
        self._database = database

    def set_notify_callback(self, callback: Callable[[str, str], Awaitable[None]]) -> None:
        """Set the callback function to notify users. Signature: async (user_id, message) -> None"""
        self._notify_callback = callback

    async def load_from_db(self) -> None:
        """Load unfired reminders from the database into the in-memory list on startup."""
        if not self._database:
            logger.warning("No database configured for ReminderService; skipping DB load")
            return

        rows = await self._database.get_active_reminders()
        for row in rows:
            reminder = Reminder(
                id=row["id"],
                user_id=row["user_id"],
                message=row["message"],
                due_at=datetime.fromisoformat(row["due_at"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                fired=False,
            )
            self._reminders.append(reminder)

        # Update counter so new IDs don't collide with DB IDs
        if self._reminders:
            max_num = 0
            for r in self._reminders:
                try:
                    num = int(r.id.replace("rem-", ""))
                    if num > max_num:
                        max_num = num
                except ValueError:
                    pass
            self._counter = max_num

        logger.info(f"Loaded {len(rows)} active reminders from database")

    async def add_reminder(self, user_id: str, message: str, due_at: datetime) -> Reminder:
        """Add a new reminder (persists to DB and keeps in memory)."""
        self._counter += 1
        reminder = Reminder(
            id=f"rem-{self._counter}",
            user_id=user_id,
            message=message,
            due_at=due_at,
        )
        self._reminders.append(reminder)

        # Persist to database
        if self._database:
            try:
                await self._database.save_reminder(
                    id=reminder.id,
                    user_id=reminder.user_id,
                    message=reminder.message,
                    due_at=reminder.due_at.isoformat(),
                    created_at=reminder.created_at.isoformat(),
                )
            except Exception as e:
                logger.warning(f"Failed to persist reminder to DB: {e}")

        logger.info(f"Reminder added: '{message}' due at {due_at} for user {user_id}")
        return reminder

    async def list_reminders(self, user_id: str) -> list[Reminder]:
        """List active reminders for a user (queries database for accuracy)."""
        if self._database:
            try:
                rows = await self._database.get_user_reminders(user_id)
                return [
                    Reminder(
                        id=row["id"],
                        user_id=row["user_id"],
                        message=row["message"],
                        due_at=datetime.fromisoformat(row["due_at"]),
                        created_at=datetime.fromisoformat(row["created_at"]),
                        fired=False,
                    )
                    for row in rows
                ]
            except Exception as e:
                logger.warning(f"DB query failed, falling back to in-memory: {e}")

        return [r for r in self._reminders if r.user_id == user_id and not r.fired]

    async def cancel_reminder(self, reminder_id: str) -> bool:
        """Cancel a reminder by ID."""
        for r in self._reminders:
            if r.id == reminder_id and not r.fired:
                r.fired = True
                # Mark in database too
                if self._database:
                    try:
                        await self._database.mark_reminder_fired(reminder_id)
                    except Exception as e:
                        logger.warning(f"Failed to mark reminder fired in DB: {e}")
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
                                # Mark as fired in database
                                if self._database:
                                    try:
                                        await self._database.mark_reminder_fired(r.id)
                                    except Exception as e:
                                        logger.warning(f"Failed to mark reminder fired in DB: {e}")
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
