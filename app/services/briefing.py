"""Daily briefing service — generates a morning summary on login."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.services.ollama import OllamaClient
from app.storage.database import Database

logger = logging.getLogger(__name__)

# Only show briefing once per session (cooldown in seconds)
_BRIEFING_COOLDOWN = 3600  # 1 hour
_last_briefing: dict[str, float] = {}


def _should_show_briefing(user_id: str) -> bool:
    last = _last_briefing.get(user_id, 0)
    return (time.monotonic() - last) > _BRIEFING_COOLDOWN


def _mark_briefing_shown(user_id: str) -> None:
    _last_briefing[user_id] = time.monotonic()


def _get_greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    elif hour < 17:
        return "Good afternoon"
    else:
        return "Good evening"


def _scan_recent_files(hours: int = 24) -> dict:
    """Scan common folders for recently modified files."""
    home = Path.home()
    cutoff = datetime.now() - timedelta(hours=hours)
    results = {"new_downloads": [], "modified_files": [], "recent_screenshots": 0}

    # Check Downloads for new files
    downloads = home / "Downloads"
    if downloads.exists():
        for f in downloads.iterdir():
            try:
                if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) > cutoff:
                    results["new_downloads"].append(f.name)
            except (OSError, ValueError):
                continue

    # Check Desktop for changes
    for desktop_path in [home / "OneDrive" / "Desktop", home / "Desktop"]:
        if desktop_path.exists():
            for f in desktop_path.iterdir():
                try:
                    if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) > cutoff:
                        results["modified_files"].append(f.name)
                except (OSError, ValueError):
                    continue
            break

    # Count recent screenshots
    for screenshots_path in [
        home / "OneDrive" / "Pictures" / "Screenshots",
        home / "Pictures" / "Screenshots",
    ]:
        if screenshots_path.exists():
            for f in screenshots_path.iterdir():
                try:
                    if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) > cutoff:
                        results["recent_screenshots"] += 1
                except (OSError, ValueError):
                    continue
            break

    return results


def _scan_git_repos() -> list[dict]:
    """Check known repos for uncommitted changes."""
    import subprocess
    home = Path.home()
    repos = []

    # Check common project locations
    for repo_path in [
        home / "Downloads" / "openclaw-transfer",
    ]:
        if (repo_path / ".git").exists():
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(repo_path),
                    capture_output=True, text=True, timeout=5,
                )
                changed = len([l for l in result.stdout.strip().split("\n") if l.strip()])
                if changed > 0:
                    repos.append({"name": repo_path.name, "changes": changed})
            except Exception:
                continue

    return repos


async def generate_briefing(
    user_name: str,
    user_id: str,
    ollama: OllamaClient,
    database: Database,
) -> str | None:
    """Generate a daily briefing for the user. Returns None if cooldown active."""
    if not _should_show_briefing(user_id):
        return None

    _mark_briefing_shown(user_id)

    try:
        greeting = _get_greeting()
        now = datetime.now()
        date_str = now.strftime("%A, %B %d, %Y")
        time_str = now.strftime("%I:%M %p")

        # Gather context
        file_activity = _scan_recent_files(hours=24)
        git_repos = _scan_git_repos()

        # Get recent conversation count
        conv_count = 0
        try:
            conv = await database.get_or_create_conversation(user_id)
            conv_count = len(conv.messages)
        except Exception:
            pass

        # Build context for LLM
        context_parts = []

        if file_activity["new_downloads"]:
            dl_list = file_activity["new_downloads"][:5]
            context_parts.append(f"New downloads ({len(file_activity['new_downloads'])}): {', '.join(dl_list)}")

        if file_activity["modified_files"]:
            mod_list = file_activity["modified_files"][:5]
            context_parts.append(f"Modified desktop files: {', '.join(mod_list)}")

        if file_activity["recent_screenshots"] > 0:
            context_parts.append(f"Screenshots taken today: {file_activity['recent_screenshots']}")

        if git_repos:
            for repo in git_repos:
                context_parts.append(f"Git repo '{repo['name']}' has {repo['changes']} uncommitted changes")

        if conv_count > 0:
            context_parts.append(f"You have {conv_count} messages in your current conversation")

        context = "\n".join(context_parts) if context_parts else "No notable activity detected."

        # Generate briefing with Ollama
        prompt = f"""Generate a brief, friendly daily briefing for {user_name.split()[0] if user_name else 'the user'}.
Today is {date_str}, {time_str}.

Activity summary:
{context}

Rules:
- Keep it to 4-6 lines max
- Start with "{greeting}, {user_name.split()[0] if user_name else 'there'}!"
- Mention the date
- Summarize the activity naturally
- End with 1-2 actionable suggestions based on the activity
- Be concise and helpful, like a smart assistant
- Do NOT use emojis excessively, keep it professional"""

        result = await ollama.chat(messages=[
            {"role": "system", "content": "You are MyAi, a personal AI assistant. Generate a brief daily briefing."},
            {"role": "user", "content": prompt},
        ])
        briefing = result.get("message", {}).get("content", "").strip()

        if briefing:
            return briefing

    except Exception as e:
        logger.warning(f"Failed to generate briefing: {e}")

    return None
