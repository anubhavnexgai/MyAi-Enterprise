# MyAi — New Features (2026-04-27)

OpenClaw + NemoClaw concepts ported into MyAi as native Python. Free stack only — Ollama, local SQLite, free tier channels. **Tool count: 24 → 32.** Nine new capability pillars layered on top of the existing chat agent.

---

## Quick reference

| # | Pillar | What it adds | New tools |
|---|---|---|---|
| 1 | Personas + workspace | Editable persona files, `@sam`/`@polly` switching | — |
| 2 | Memory dreaming | Auto journal + daily diary + fact extraction into `user.md` | `consolidate_memory` |
| 3 | Reliability layer | `policy.yaml`, approval queue, audit log | — |
| 4 | Autonomy loop | Goal planner + executor with replan-on-failure | `start_goal`, `goal_status`, `cancel_goal` |
| 5 | Critic | Second-opinion LLM check before risky actions | — |
| 6 | Vision | Image + screen description via LLaVA on Ollama | `describe_image`, `describe_screen` |
| 7 | Skill self-creation | Agent generates new tools on demand | `skill_factory_create`, `skill_factory_install` |
| 8 | Channel gateway | Telegram + WhatsApp + future channels behind one interface | — |
| 9 | Heartbeat | Per-persona scheduled proactive ticks | — |

---

## How to start the server

```powershell
cd C:\Users\anubh\Downloads\myai
.venv\Scripts\python.exe -m app.main --web-only
```

Web UI: `http://localhost:8001`. Send messages there for testing.

Optional environment variables (set in PowerShell before launching):

```powershell
# Telegram
$env:TELEGRAM_BOT_TOKEN = "<token from @BotFather>"
$env:TELEGRAM_CHAT_ID   = "<your numeric chat id; bot logs it on first msg>"

# Heartbeat (off by default)
$env:MYAI_HEARTBEAT          = "on"
$env:MYAI_HEARTBEAT_INTERVAL = "1800"   # seconds; default 30 min
```

---

## Pillar 1 — Personas + workspace

### What it does
Three personas seeded: **MyAi** (default), **Sam** (SDR), **Polly** (EA). Each has its own `identity.md` and `soul.md`. Mention `@sam` or `@polly` at the start of any message to route that turn through the named persona. Edit any markdown file under `app/workspace/` and the change goes live on the next chat turn — no restart.

### Files
- `app/workspace/{identity, soul, user, tools, heartbeat}.md` — global / default persona
- `app/workspace/agents/sam/{identity, soul}.md` — Sam (SDR)
- `app/workspace/agents/polly/{identity, soul}.md` — Polly (EA)
- `app/agent/persona.py` — `PersonaLoader` singleton, hot-reloads via watchdog

### How to test

In the web UI, type each of these in turn:

| Prompt | Expected behavior |
|---|---|
| `hi` | Default MyAi persona replies |
| `@sam say hi briefly` | Sam responds, more crisp / sales tone |
| `@polly what's your job` | Polly responds, EA-style |
| `@unknownpersona hello` | Falls back to default; doesn't error |
| `what is @sam doing here` | Mid-sentence @ → default (not a switch) |

**Hot-reload check:** edit `app/workspace/agents/sam/identity.md`, change Sam's emoji from 📈 to anything else, save. Send `@sam introduce yourself` — should reflect the edit on the very next response.

### Where to look
Server logs print `Persona switch: routing this turn to 'sam'` whenever `@sam` triggers. Audit DB rows have a `persona` column.

---

## Pillar 2 — Memory dreaming

### What it does
Every chat turn is auto-appended to a per-persona JSONL journal. A **dreaming** job (`consolidate_memory` tool) reads a day's journal, asks Ollama to write a 3–6 sentence diary, extracts durable facts about you, and appends new facts to `app/workspace/user.md` under the `<!-- DREAMING_APPEND_BELOW -->` marker. The next chat turn picks the new facts up automatically (PersonaLoader cache invalidates).

### Files
- `app/services/journal.py` — append-only JSONL writer
- `app/services/diary.py` — Ollama-driven consolidation
- Journal location: `app/workspace/journal/YYYY-MM-DD.jsonl` (default persona) or `app/workspace/agents/<name>/journal/YYYY-MM-DD.jsonl`
- Diary location: same paths, `diary/` subdir

### How to test

1. **Trigger the journal**: send 3–4 chat messages of any kind. Then check that today's journal exists:
   ```powershell
   Get-Content app\workspace\journal\$(Get-Date -Format yyyy-MM-dd).jsonl | Select-Object -First 3
   ```
   You should see one JSON line per turn with `user_msg`, `response`, `tool_calls`.

2. **Run dreaming**: in the web UI, type:
   ```
   consolidate today's memory
   ```
   The agent should call `consolidate_memory`, you'll see something like:
   `Consolidated 4 entries → C:\Users\anubh\Downloads\myai\app\workspace\diary\2026-04-27.md. Added 1 new fact(s) to user.md.`

3. **Verify the diary file**:
   ```powershell
   Get-Content app\workspace\diary\$(Get-Date -Format yyyy-MM-dd).md
   ```

4. **Verify user.md got patched**:
   ```powershell
   Get-Content app\workspace\user.md
   ```
   Look for new bullets under `<!-- DREAMING_APPEND_BELOW -->`.

5. **Verify the fact persists**: send a follow-up message that depends on the new fact (e.g. if it learned "user prefers paragraphs over bullets," say `summarize this in any format you like`). The response should respect the preference.

### Per-persona journals
Every `@sam` turn writes to `app/workspace/agents/sam/journal/YYYY-MM-DD.jsonl`. Same for Polly. Verify with:
```powershell
Get-ChildItem app\workspace\agents\*\journal -Recurse
```

---

## Pillar 3 — Reliability layer (policy + approval + audit)

### What it does
A NemoClaw-style governance plane sitting in front of every tool call:
- **policy.yaml** declares which tools require approval, which need critic review, and which are blocked.
- Approval-required tools land in a SQLite **pending_actions** queue and either wait for a ✅ or return a "queued for approval" message immediately, depending on the caller.
- Every tool call (and every governance decision) is written append-only to an **audit log** in `data/governance.db`.

### Files
- `config/policy.yaml` — declarative policy, hot-reloadable
- `app/services/policy.py` — PolicyService singleton
- `app/services/approval.py` — pending queue + async `wait_for(id)`
- `app/services/audit.py` — append-only audit log
- `data/governance.db` — SQLite (auto-created)

### Default approval-required tools
`send_email`, `send_whatsapp`, `write_file`, `type_in_app`, `browse_web`, `mcp_call`, `skill_factory_install`.

### How to test

1. **Approval queue fires**: in the web UI:
   ```
   send an email to test@example.com saying hello
   ```
   Expected response (one of):
   ```
   🔒 `send_email` requires approval. Queued as #1.
   Approve via the web admin UI, a WhatsApp/Telegram ✅ reply, or
   `python -m app.scripts.approve 1`.
   ```

2. **Hot-reload of policy**: edit `config/policy.yaml`, remove `send_email` from `tools.approval_required`, save. Send the same prompt again — it should now go straight through (Outlook draft will pop up).

3. **Audit log accumulates**: after a few tool calls, inspect:
   ```powershell
   .venv\Scripts\python.exe -c "from app.services.audit import get_audit; import json; print(json.dumps(get_audit().tail(5), indent=2))"
   ```

4. **Network rules**: edit `policy.yaml`, change `network.on_unlisted` to `block`. Try a `web_search` for an unlisted domain — should be refused. (Network rules currently advisory — block enforcement on `web_search` is a future hardening step.)

5. **Inspect pending queue from a separate shell**:
   ```powershell
   .venv\Scripts\python.exe -c "from app.services.approval import get_approval; import json; print(json.dumps(get_approval().list_pending(), indent=2))"
   ```

6. **Approve from CLI**:
   ```powershell
   .venv\Scripts\python.exe -c "from app.services.approval import get_approval; print(get_approval().approve(1, by='cli'))"
   ```
   The waiting tool (if any) resumes; the audit log gains an `approved` row.

### Existing destructive guardrail still works
```
delete everything on my desktop
```
Should respond `Action blocked: ...` — that's the existing `GuardrailsService` (older layer, kept in place).

---

## Pillar 4 — Autonomy (planner + executor)

### What it does
Give MyAi a high-level goal; the planner asks Ollama to decompose it into steps; the executor runs them sequentially, replanning **once** if a step fails. State is persisted in `data/governance.db` so goals survive restarts.

### Files
- `app/services/planner.py` — `Planner.plan()` and `replan()`
- `app/services/autonomy.py` — `AutonomyService` with `goals` + `steps` SQLite tables
- New tools: `start_goal`, `goal_status`, `cancel_goal`

### How to test

1. **Easy goal**:
   ```
   start a goal to count how many python files are in my project
   ```
   Expected: a response like
   ```
   🤖 Goal #1 started (3 steps).
     1. List the contents of C:\Users\anubh\Downloads\myai
     2. Filter for .py files
     3. Report the count
   Use goal_status(1) to check progress.
   ```

2. **Check progress**:
   ```
   what's the status of goal 1
   ```
   Or directly: `goal_status(1)`. You'll see ✓ for done steps, ▶ for running, · for pending.

3. **Replan on failure**: trigger a goal that's likely to need replanning:
   ```
   start a goal to find a file called notarealfile.xyz in my downloads and read it
   ```
   First step succeeds (search), second fails (not found), planner replans, second plan completes or fails cleanly.

4. **Cancel a goal**:
   ```
   cancel goal 1
   ```

5. **List all goals**: from the Python REPL or a script:
   ```powershell
   .venv\Scripts\python.exe -c "from app.services.autonomy import get_autonomy; from app.agent.tools import ToolRegistry; print('see goals via goal_status tool')"
   ```

### Note on approval-gated steps
If the planner picks a tool that needs approval (e.g. `send_email`), the autonomy executor calls it with `wait_for_approval=True` — meaning the goal pauses up to 10 minutes waiting for ✅. Approve via channel or CLI, the goal resumes.

---

## Pillar 5 — Critic

### What it does
Tools listed under `policy.tools.critic_review` get a second-opinion LLM call before they run. The critic returns `{approve, concern_level, reasoning}`. If the critic objects, the action is auto-queued for approval — even if it wasn't already in `approval_required`. The critic's reasoning is attached to the queued action.

### Files
- `app/services/critic.py`

### Default critic-review tools
`browse_web`, `type_in_app`.

### How to test

1. **Risky browse_web**:
   ```
   go to evil-malware-download-site.example using the browser
   ```
   Expected: queued for approval with critic's reasoning. Check the audit log:
   ```powershell
   .venv\Scripts\python.exe -c "from app.services.audit import get_audit; import json; rows = [r for r in get_audit().tail(20) if r['decision'].startswith('critic_')]; print(json.dumps(rows, indent=2))"
   ```
   You should see a `critic_object` row with reasoning like *"Request involves visiting a known malicious-looking site"*.

2. **Reasonable browse_web**:
   ```
   go to github.com using the browser
   ```
   Critic should approve, then the policy-level approval queue still fires (browse_web is also in `approval_required`). So you'll still see the 🔒 queued message — but the audit log will show `critic_approve` first.

---

## Pillar 6 — Vision (LLaVA via Ollama)

### What it does
Multimodal image understanding using whatever vision model `policy.yaml :: models.routes.vision` points at (default `llava:7b`). Free, local. Two new tools.

### Files
- `app/services/vision.py`

### Setup (one-time)
```powershell
ollama pull llava:7b
```
Or use any other multimodal model and update `models.routes.vision` in `policy.yaml`.

### How to test

1. **Describe an image file**:
   ```
   describe the image at C:\Users\anubh\OneDrive\Pictures\Screenshots\Screenshot.png
   ```
   (Pick any image you have.) The agent calls `describe_image`, returns a 2–4 sentence description.

2. **Describe the current screen**:
   ```
   what's on my screen right now
   ```
   Takes a fresh screenshot, runs it through LLaVA, describes it.

3. **Specific question**:
   ```
   describe my screen — is there any error message visible
   ```

### If LLaVA isn't pulled
You'll get a graceful message:
```
Vision model 'llava:7b' unavailable. Try `ollama pull llava:7b`.
```
This is the expected behavior, not a bug.

---

## Pillar 7 — Skill self-creation factory

### What it does
The agent generates new Python tools on demand, lints them (AST + banned-imports whitelist), stages them under `app/workspace/skills/_staging/<name>.py`, and installs them on approval. Installed skills auto-load at every server restart.

### Files
- `app/services/skill_factory.py`
- `app/workspace/skills/` — installed skills
- `app/workspace/skills/_staging/` — staged but not yet installed
- New tools: `skill_factory_create`, `skill_factory_install`

### Allowed imports in generated code
`json, datetime, pathlib, re, math, hashlib, base64, textwrap, urllib.parse, httpx, typing, asyncio`. Anything else fails the lint.

### How to test

1. **Generate a simple tool**:
   ```
   create a tool that converts celsius to fahrenheit, name it celsius_to_f
   ```
   Expected response:
   ```
   📦 Staged skill `celsius_to_f` at C:\Users\anubh\Downloads\myai\app\workspace\skills\_staging\celsius_to_f.py.
   
   ```python
   META = {"name": "celsius_to_f", "description": "Convert Celsius to Fahrenheit"}
   
   async def run(**kwargs) -> str:
       try:
           c = float(kwargs.get("c", 0))
           return str(c * 9/5 + 32)
       except Exception as e:
           return f"Error: {e}"
   ```
   
   Review the code, then call skill_factory_install(name='celsius_to_f') ...
   ```

2. **Install it**:
   ```
   install the celsius_to_f skill
   ```
   This is **approval-required**, so you'll first see a 🔒 queued message. Approve via CLI:
   ```powershell
   .venv\Scripts\python.exe -c "from app.services.approval import get_approval; print(get_approval().approve(<id>, by='cli'))"
   ```

3. **Use the new skill**:
   ```
   use the celsius_to_f skill to convert 100 celsius
   ```
   The agent should now know about `celsius_to_f` (it was hot-registered in `tool_registry._tools`) and call it.

4. **Try a banned-import skill** (should be rejected):
   ```
   make a tool that runs the dir command via subprocess, name it dir_runner
   ```
   Expected: `Skill creation rejected: banned substring: 'subprocess'`

### Persistence test
Restart the server. The `celsius_to_f` skill should still be available because installed skills auto-load from `app/workspace/skills/*.py` at startup.

---

## Pillar 8 — Channel gateway + Telegram

### What it does
A unified `Channel` interface with two adapters: **Telegram** (long-poll Bot API, parses `approve N` replies into approval decisions automatically) and **WhatsApp** (thin wrapper over the existing Twilio service). Any tool queued for approval automatically pings every linked channel with the action ID and reason.

### Files
- `app/services/channels.py`

### How to test

#### Telegram setup
1. Talk to [@BotFather](https://t.me/BotFather) on Telegram, `/newbot`, copy the token.
2. Send any message to your new bot from your personal Telegram.
3. Set env vars:
   ```powershell
   $env:TELEGRAM_BOT_TOKEN = "<token>"
   ```
4. Start the server. Watch the logs for:
   ```
   Telegram first-message chat_id=<NUMBER> — set TELEGRAM_CHAT_ID to this
   ```
5. Set that chat ID:
   ```powershell
   $env:TELEGRAM_CHAT_ID = "<chat id from logs>"
   ```
6. Restart the server.

#### Send a test
1. In the web UI, type:
   ```
   send an email to test@example.com saying hi
   ```
2. The action queues for approval. **Within 1–2 seconds your Telegram should ping** with:
   ```
   🔒 MyAi wants to run send_email (action #1).
   Reason: policy.approval_required
   
   Reply approve 1 or reject 1 <note> to decide.
   ```
3. Reply on Telegram: `approve 1`
4. The web UI's pending request resumes, Outlook draft opens.

#### Reject path
1. Trigger another approval-required action.
2. Reply on Telegram: `reject 2 looks suspicious`
3. The web UI shows: `❌ send_email was rejected by telegram: looks suspicious`

#### Programmatic test
```powershell
.venv\Scripts\python.exe -c @"
import asyncio
from app.services.channels import get_channel_gateway
gw = get_channel_gateway()
print('Enabled:', [c.name for c in gw.enabled_channels()])
asyncio.run(gw.broadcast('user', 'Test broadcast from MyAi'))
"@
```
You should receive the message on every enabled channel.

---

## Pillar 9 — Heartbeat

### What it does
Each persona that has a non-empty `heartbeat.md` gets its own asyncio loop. Every N seconds it reads the heartbeat checklist, sends a synthesized `[HEARTBEAT]` prompt to the agent (with `@persona` prefix), and either suppresses output (if response is `HEARTBEAT_OK`) or broadcasts the response via the channel gateway. Duplicate consecutive responses are also suppressed.

### Files
- `app/services/heartbeat.py`

### How to test

1. **Enable heartbeat**:
   ```powershell
   $env:MYAI_HEARTBEAT          = "on"
   $env:MYAI_HEARTBEAT_INTERVAL = "120"   # 2 min for fast feedback
   .venv\Scripts\python.exe -m app.main --web-only
   ```

2. **Watch the logs** — within 30s you should see:
   ```
   Heartbeat started for 'default' every 120s
   Heartbeat started for 'sam' every 120s
   Heartbeat started for 'polly' every 120s
   ```

3. **Wait ~2 min**. The heartbeat fires; if there's anything actionable (an overdue reminder, a new file in Downloads, etc.), it broadcasts via channels:
   ```
   💓 heartbeat (polly)
   
   Good morning. You have nothing on the schedule, the latest file in
   Downloads is foo.pdf (2 mins ago).
   ```

4. **Edit a persona's heartbeat behavior**: change `app/workspace/agents/polly/heartbeat.md` (create one — currently inherits global) to add a specific check. Wait one tick to see it run.

5. **Verify suppression**: send the same prompt twice in a row mentally — heartbeat should not spam the channels with identical messages.

---

## Verifying the whole system at once

The repo includes `_continuous_test.py` — a 90-minute test harness that drives ~70 prompts × ~4 passes through the real `AgentCore`, validating each against expected behavior signals. Auto-approves any pending approvals along the way.

```powershell
$env:MYAI_TEST_DURATION_MIN = "10"   # short version, 10 min
PYTHONIOENCODING=utf-8 .venv\Scripts\python.exe _continuous_test.py
```

While running, tail:
```powershell
Get-Content _continuous_test.summary.md -Wait
```

Output: `_continuous_test.log.jsonl` (one JSON line per test) and `_continuous_test.summary.md` (human-readable rolling summary).

This won't pollute your real audit log — it uses a temp governance DB.

---

## Common gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| `Vision model 'llava:7b' unavailable` | LLaVA not pulled | `ollama pull llava:7b` |
| Telegram silent on approval ping | `TELEGRAM_CHAT_ID` not set | First send any msg to bot, copy chat_id from server logs, set env var, restart |
| Approval queue not draining | No approver is approving | Approve via Telegram (`approve N`), CLI, or set `approvals.auto_approve_after_seconds: 60` in `policy.yaml` for testing |
| Skill factory rejects code | Model used a banned import | Check the rejection reason; either tweak the prompt or extend `ALLOWED_IMPORTS` in `skill_factory.py` |
| Heartbeat isn't firing | `MYAI_HEARTBEAT` env not set or empty `heartbeat.md` | Set `$env:MYAI_HEARTBEAT = "on"` and ensure `app/workspace/heartbeat.md` is non-empty |
| `@unknownpersona ...` doesn't switch | That's correct — unknown personas fall back to default | If you want a new persona, create `app/workspace/agents/<name>/identity.md` |
| Edits to `policy.yaml` don't take effect | Watcher not running | Check server logs for `PolicyService: watching ...`. If absent, restart the server. |

---

## File map (everything new)

```
app/
  agent/
    persona.py                  ← Pillar 1: PersonaLoader singleton
  services/
    journal.py                  ← Pillar 2: episodic JSONL journal
    diary.py                    ← Pillar 2: dreaming consolidation
    policy.py                   ← Pillar 3: PolicyService
    audit.py                    ← Pillar 3: append-only audit log
    approval.py                 ← Pillar 3: pending-actions queue
    planner.py                  ← Pillar 4: goal decomposer
    autonomy.py                 ← Pillar 4: goal executor
    critic.py                   ← Pillar 5: second-opinion critic
    vision.py                   ← Pillar 6: LLaVA wrapper
    skill_factory.py            ← Pillar 7: agent-authored tools
    channels.py                 ← Pillar 8: ChannelGateway + Telegram
    heartbeat.py                ← Pillar 9: per-persona scheduled ticks
  workspace/
    identity.md, soul.md, user.md, tools.md, heartbeat.md
    agents/sam/identity.md, soul.md
    agents/polly/identity.md, soul.md
    skills/                     ← installed skills land here
      _staging/                 ← staged-but-not-installed
config/
  policy.yaml                   ← Pillar 3: declarative governance
data/
  governance.db                 ← SQLite for audit + approval + autonomy

_continuous_test.py             ← optional bulk regression test
```

Modified files:
- `app/agent/core.py` — persona detection + journal capture via ContextVar
- `app/agent/tools.py` — full execute pipeline + 8 new tool methods + skill auto-load
- `app/agent/prompts.py` — 8 new tool definitions
- `app/main.py` — startup hooks for persona watcher, policy watcher, channel gateway, approval notifier, heartbeat

---

## What's intentionally NOT shipped (yet)

- True OS-level sandbox for skill_factory (it's lint+approval, not container isolation)
- Audit log UI (use SQLite client or `get_audit().tail(N)`)
- Voice (planned: local Whisper + Piper; not yet wired)
- Browser STT in the web UI (planned via Web Speech API)
- Google Meet / Zoom auto-join
- Network policy *enforcement* (currently advisory; only logged in audit)
- RBAC ↔ policy tie-in (RBAC exists, policy exists, they don't yet talk)

These are the right next steps — none are blockers for the current feature set.
