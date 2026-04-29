# Heartbeat

Every N minutes (default 30, configurable per-persona), the heartbeat loop
wakes the agent and feeds it this file plus a snapshot of recent events.
The agent decides whether to do anything, then either acts or returns
`HEARTBEAT_OK` to stay silent.

## What to check on every heartbeat

1. **Pending reminders.** Any reminder whose due time has passed since the
   last heartbeat? If yes, fire it.
2. **New files in watched folders.** Anything the user might want triaged?
   (Don't notify for every file — only ones that look interesting:
   PDFs/docs/spreadsheets in Downloads, screenshots they might want to act on.)
3. **Pending approvals.** Any actions in the approval queue that have been
   sitting for >1 hour? If yes, ping the user once (don't re-ping).
4. **Heartbeat-specific tasks.** See "Recurring tasks" below.

## Recurring tasks

(This section is owned by the user and the dreaming loop. Edit freely.)

- **08:30 daily** — morning briefing: weather, calendar for today, any
  overdue reminders, top 3 unread emails (when email is wired).
- **22:00 daily** — kick off the dreaming/consolidation job for today.

## Output protocol

- If nothing needs doing, output exactly: `HEARTBEAT_OK`
- If actions are needed, perform them via tool calls. After all actions,
  output a brief one-line summary so the WhatsApp/Telegram channel can post
  it. Do NOT post if nothing changed.
- NEVER spam. If you'd send the same message you sent on the last heartbeat,
  stay silent.

## Cost discipline

Heartbeats consume tokens proportionally to frequency × prompt size. Keep
this file short. If a recurring task gets long, move its details into a
separate file and reference it from here.
