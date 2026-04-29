"""Pre-intercept regex shortcuts for AgentCore.

Small LLMs (qwen2.5:7b) are unreliable at tool-calling discipline — they
sometimes chat instead of emitting a tool block. For high-value, easily
recognised intents we pattern-match the user's message and call the right
tool deterministically, bypassing the LLM entirely.

These were originally embedded inside main.py's WebSocket handler; moving
them here means every entry path (web UI, WhatsApp, scheduled jobs,
heartbeat, tests) gets the same reliability gain.

Public API:
    await try_intercept(text, agent, user_id) -> str | None
        Returns the response text when an intercept handled the turn,
        or None when nothing matched (caller should fall through to LLM).

Order matters — earlier intercepts win. The destructive-action blocker
must stay first.
"""
from __future__ import annotations

import logging
import os
import re
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.core import AgentCore

logger = logging.getLogger(__name__)


# ---- regex catalogue (compiled once at import time) -----------------------

_RE_DESTRUCTIVE = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+|i want you to\s+|go\s+)?"
    r"(delete|remove|erase|wipe|destroy|format|shred|empty)\b.+"
    r"\b(all|every|everything|files?|folders?|desktop|documents?|downloads?|directory|disk|drive)\b",
    re.IGNORECASE,
)

_RE_REMINDER = re.compile(
    r"(?:remind me|set a reminder|reminder)\s+"
    r"(in\s+\d+\s*(?:minutes?|mins?|hours?|hrs?|seconds?)"
    r"|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
    r"|tomorrow\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)"
    r"\s+(?:to\s+)?(.+)",
    re.IGNORECASE,
)

_RE_EMAIL = re.compile(
    r"(?:send|draft|write)\s+(?:an?\s+)?(?:email|mail)\s+to\s+([\w.+-]+@[\w.-]+)"
    r"(?:\s+with\s+subject\s+[\"']?(.+?)[\"']?)?"
    r"\s+(?:saying|with\s+body|body|that|with\s+message|about)\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)

_RE_WHATSAPP = re.compile(
    r"(?:send|write)\s+(?:a\s+)?(?:whatsapp|wa)\s+(?:message\s+)?"
    r"to\s+([\d+]+)\s+(?:saying|that|with\s+message)\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)

_RE_APP_LAUNCH = re.compile(
    r"(?:launch|start|run|open)\s+(?:the\s+|a\s+)?(?:app\s+|application\s+)?"
    r"(microsoft\s+word|ms\s+word|microsoft\s+excel|ms\s+excel|microsoft\s+powerpoint|ms\s+powerpoint"
    r"|word|excel|powerpoint|notepad|calculator|chrome|firefox|code|vscode|vs\s+code"
    r"|outlook|teams|slack|explorer|file\s+explorer|paint|cmd|powershell|terminal"
    r"|wordpad|task\s+manager|settings|snipping\s+tool|(?:the\s+)?browser)$",
    re.IGNORECASE,
)
_APP_NORMALIZE = {
    "microsoft word": "word", "ms word": "word",
    "microsoft excel": "excel", "ms excel": "excel",
    "microsoft powerpoint": "powerpoint", "ms powerpoint": "powerpoint",
    "the browser": "chrome", "browser": "chrome",
}

_RE_OPEN_URL = re.compile(
    r"(?:open|go to|visit|navigate to)\s+(?:the\s+)?(?:website\s+|site\s+|url\s+)?"
    r"(https?://\S+|(?:www\.)?[\w.-]+\.(?:com|org|io|dev|ai|net|co|edu|gov)(?:/\S*)?)\s*$",
    re.IGNORECASE,
)

_RE_LATEST_FILE = re.compile(
    r"(?:open|show|view)\s+(?:the\s+|my\s+)?(?:latest|newest|most recent|last)\s+"
    r"(?:file\s+)?(?:I\s+)?(?:downloaded|in\s+downloads?|from\s+downloads?|in\s+my\s+downloads?)?$",
    re.IGNORECASE,
)

_RE_OPEN_FILE = re.compile(
    r"(?:open|show|view)\s+(?:the\s+|my\s+|this\s+)?(?:file\s+)?(.+?)(?:\s+file)?$",
    re.IGNORECASE,
)

_RE_BROWSE = re.compile(
    r"(?:browse|go to|navigate to|visit)\s+(.+?)(?:\s+and\s+(.+))?$",
    re.IGNORECASE,
)

_RE_ORCHESTRATE = re.compile(
    r"(?:orchestrate|do all|do these|simultaneously|in parallel)[:\s]+(.+)",
    re.IGNORECASE | re.DOTALL,
)

_RE_TYPE_IN_APP = re.compile(
    r"(?:open)\s+(\w+)\s+(?:and\s+)?(?:write|type|put|create|draft)\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)


# ---- main entry point -----------------------------------------------------

async def try_intercept(text: str, agent: AgentCore, user_id: str) -> str | None:
    """Try every intercept in order; return response string on first match."""
    if not text or not text.strip():
        return None
    text = text.strip()
    tools = agent.tools
    ollama = agent.ollama
    if tools is None:
        return None

    # ---- 1. Destructive blocker (highest priority) -----------------------
    if _RE_DESTRUCTIVE.match(text):
        return ("I cannot perform destructive actions like deleting or "
                "removing files. This action is blocked by security policy "
                "for your safety.")

    # ---- 2. Reminder -----------------------------------------------------
    m = _RE_REMINDER.match(text)
    if m:
        reminder_service = getattr(tools, "_reminder_service", None)
        if reminder_service is not None:
            time_expr = m.group(1).strip()
            msg = m.group(2).strip()
            try:
                due = reminder_service.parse_time_expression(time_expr)
                if due:
                    await reminder_service.add_reminder(user_id, msg, due)
                    return f"Reminder set for {due.strftime('%I:%M %p')}: {msg}"
            except Exception as exc:
                logger.warning("Reminder intercept failed: %s", exc)

    # ---- 3. Email (LLM drafts body, code sends) --------------------------
    m = _RE_EMAIL.match(text)
    if m:
        to = m.group(1).strip()
        subject_hint = (m.group(2) or "").strip()
        body_hint = m.group(3).strip()
        try:
            draft_prompt = (
                f"Draft a professional email.\n"
                f"To: {to}\n"
                f"{'Subject: ' + subject_hint if subject_hint else 'Generate an appropriate subject.'}\n"
                f"The email should be about: {body_hint}\n\n"
                f"Reply in this EXACT format (no other text):\n"
                f"SUBJECT: <subject line>\n"
                f"BODY:\n<email body>"
            )
            draft = await ollama.chat(messages=[
                {"role": "system",
                 "content": "You draft professional emails. Reply ONLY in "
                            "the format requested. Sign off as Anubhav Choudhury."},
                {"role": "user", "content": draft_prompt},
            ])
            draft_text = draft.get("message", {}).get("content", "").strip()

            subject = subject_hint or "Message from MyAi"
            body = body_hint
            sm = re.search(r"SUBJECT:\s*(.+)", draft_text)
            bm = re.search(r"BODY:\s*\n?([\s\S]+)", draft_text)
            if sm:
                subject = sm.group(1).strip()
            if bm:
                body = bm.group(1).strip()
            return await tools._send_email(to, subject, body)
        except Exception as exc:
            logger.warning("Email intercept failed: %s — falling through", exc)

    # ---- 4. WhatsApp -----------------------------------------------------
    m = _RE_WHATSAPP.match(text)
    if m:
        try:
            return await tools._send_whatsapp(m.group(1).strip(), m.group(2).strip())
        except Exception as exc:
            logger.warning("WhatsApp intercept failed: %s", exc)

    # ---- 5. App launch ---------------------------------------------------
    m = _RE_APP_LAUNCH.match(text)
    if m:
        app = m.group(1).strip().lower()
        app = _APP_NORMALIZE.get(app, app)
        try:
            return await tools._app_launcher(app)
        except Exception as exc:
            logger.warning("App-launch intercept failed: %s", exc)

    # ---- 6. Open URL -----------------------------------------------------
    m = _RE_OPEN_URL.match(text)
    if m:
        url = m.group(1).strip()
        if not url.startswith("http"):
            url = "https://" + url
        try:
            webbrowser.open(url)
            return f"Opened {url} in your browser."
        except Exception as exc:
            logger.warning("Open-URL intercept failed: %s", exc)

    # ---- 7. Latest file --------------------------------------------------
    m = _RE_LATEST_FILE.match(text)
    if m:
        try:
            dl_dirs = [Path.home() / "Downloads", Path.home() / "OneDrive" / "Downloads"]
            all_files = []
            for d in dl_dirs:
                if d.exists():
                    all_files.extend(
                        f for f in d.iterdir()
                        if f.is_file() and not f.name.startswith(".")
                    )
            if not all_files:
                return "No files found in your Downloads folder."
            all_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            latest = all_files[0]
            os.startfile(str(latest))
            return f"Opened {latest.name} (most recently modified file in Downloads)."
        except Exception as exc:
            logger.warning("Latest-file intercept failed: %s", exc)

    # ---- 8. Open file by name --------------------------------------------
    m = _RE_OPEN_FILE.match(text)
    if m:
        file_query = m.group(1).strip()
        fq = file_query.lower()
        is_url = (
            fq.startswith("http")
            or (re.search(r"\.\w{2,3}$", fq) and "." in fq and " " not in fq)
        )
        is_browser_task = any(kw in text.lower() for kw in [
            "browse", "browser", "in the browser", "and tell me",
            "and search", "trending",
        ])
        if not is_url and not is_browser_task:
            try:
                result = await tools._open_file(file_query)
                if "not found" not in result.lower():
                    return result
            except Exception as exc:
                logger.warning("Open-file intercept failed: %s", exc)

    # ---- 9. Browse web ---------------------------------------------------
    m = _RE_BROWSE.match(text)
    if m:
        target = m.group(1).strip().lower()
        if (any(d in target for d in (".com", ".org", ".io", ".dev", ".ai", ".net"))
                or target.startswith("http")
                or "google" in target
                or "search" in text.lower()):
            try:
                return await tools._browse_web(text)
            except Exception as exc:
                logger.warning("Browse intercept failed: %s", exc)

    # ---- 10. Orchestrate -------------------------------------------------
    m = _RE_ORCHESTRATE.match(text)
    if m:
        try:
            return await tools._orchestrate(m.group(1).strip())
        except Exception as exc:
            logger.warning("Orchestrate intercept failed: %s", exc)

    # ---- 11. Open <app> and write/type <content> -------------------------
    m = _RE_TYPE_IN_APP.match(text)
    if m:
        app_name = m.group(1).strip()
        content_hint = m.group(2).strip()
        try:
            draft = await ollama.chat(messages=[
                {"role": "system",
                 "content": "You generate content as requested. Output ONLY "
                            "the content, nothing else. No explanations, no "
                            "markdown formatting, just plain text."},
                {"role": "user", "content": f"Write the following: {content_hint}"},
            ])
            content = draft.get("message", {}).get("content", "").strip()
            if content:
                await tools._type_in_app(app=app_name, text=content)
                return f"Opened {app_name} and typed the content."
        except Exception as exc:
            logger.warning("Type-in-app intercept failed: %s", exc)

    # No intercept matched — caller should fall through to the LLM
    return None
