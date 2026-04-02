from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

from app.services.file_access import FileAccessService, FileAccessError, PermissionDeniedError
from app.services.web_search import WebSearchService
from app.services.rag import RAGService

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of available tools for the agent."""

    def __init__(
        self,
        file_service: FileAccessService,
        search_service: WebSearchService,
        rag_service: RAGService,
    ):
        self.file_service = file_service
        self.search_service = search_service
        self.rag_service = rag_service

        self._tools: dict[str, Callable[..., Awaitable[str]]] = {
            "read_file": self._read_file,
            "list_directory": self._list_directory,
            "search_files": self._search_files,
            "write_file": self._write_file,
            "web_search": self._web_search,
            "rag_query": self._rag_query,
            "send_email": self._send_email,
            "send_whatsapp": self._send_whatsapp,
            "set_reminder": self._set_reminder,
            "app_launcher": self._app_launcher,
            "clipboard_read": self._clipboard_read,
            "clipboard_write": self._clipboard_write,
            "pdf_reader": self._pdf_reader,
            "csv_reader": self._csv_reader,
            "system_info": self._system_info,
            "screenshot": self._screenshot,
            "git_status": self._git_status,
            "url_summarizer": self._url_summarizer,
            "open_url": self._open_url,
            "type_in_app": self._type_in_app,
            "open_file": self._open_file,
        }

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name with given arguments."""
        if tool_name not in self._tools:
            return f"Unknown tool: {tool_name}. Available: {', '.join(self._tools.keys())}"

        try:
            result = await self._tools[tool_name](**arguments)
            return result
        except PermissionDeniedError as e:
            return f"⛔ Permission denied: {e}"
        except FileAccessError as e:
            return f"❌ File error: {e}"
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            return f"❌ Tool error: {e}"

    # ── Tool implementations ──

    async def _read_file(self, path: str) -> str:
        content = await self.file_service.read_file(path)
        # Truncate very long files for the LLM context
        if len(content) > 8000:
            return content[:8000] + f"\n\n... [truncated, {len(content)} chars total]"
        return content

    async def _list_directory(self, path: str) -> str:
        return await self.file_service.list_directory(path)

    async def _search_files(self, directory: str, pattern: str) -> str:
        return await self.file_service.search_files(directory, pattern)

    async def _write_file(self, path: str, content: str) -> str:
        return await self.file_service.write_file(path, content)

    async def _web_search(self, query: str) -> str:
        return await self.search_service.search(query)

    async def _rag_query(self, question: str) -> str:
        return await self.rag_service.query(question)

    async def _send_email(self, to: str, subject: str, body: str) -> str:
        """Create .eml draft and open in Outlook."""
        import tempfile
        import os

        # Write .eml manually to avoid MIME line wrapping
        eml_content = (
            f"To: {to}\r\n"
            f"Subject: {subject}\r\n"
            f"X-Unsent: 1\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n"
            f"\r\n"
            f"{body}"
        )

        eml_path = os.path.join(tempfile.gettempdir(), "myai_draft.eml")
        with open(eml_path, "w", encoding="utf-8") as f:
            f.write(eml_content)

        try:
            os.startfile(eml_path)
            return (
                f"Email draft opened in Outlook.\n"
                f"To: {to}\n"
                f"Subject: {subject}\n\n"
                f"Review and click Send."
            )
        except Exception as e:
            return f"Failed to open email: {e}"

    _reminder_service = None  # Set by main.py
    _reminder_user_id = None  # Set per-request

    async def _set_reminder(self, time: str, message: str) -> str:
        """Set a reminder using the reminder service."""
        if not self._reminder_service:
            return "Reminder service is not available."

        from app.services.reminders import ReminderService
        due_at = ReminderService.parse_time_expression(time)
        if not due_at:
            return f"Couldn't understand the time: '{time}'. Try 'in 5 minutes', 'at 3pm', or 'tomorrow at 9am'."

        user_id = self._reminder_user_id or "default"
        reminder = await self._reminder_service.add_reminder(user_id, message, due_at)
        return (
            f"Reminder set!\n"
            f"Message: {message}\n"
            f"Due: {due_at.strftime('%I:%M %p, %B %d')}"
        )

    async def _send_whatsapp(self, phone: str, message: str) -> str:
        """Open WhatsApp Web with a pre-filled message."""
        import subprocess
        from urllib.parse import quote

        # Clean phone number — remove spaces, dashes, plus
        clean_phone = phone.replace(" ", "").replace("-", "").replace("+", "")

        # Use wa.me URL which opens WhatsApp Web or desktop app
        wa_url = f"https://wa.me/{clean_phone}?text={quote(message)}"

        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", wa_url],
                creationflags=0x08000000,
            )
            return (
                f"WhatsApp message drafted.\n"
                f"To: {phone}\n"
                f"Message: {message}\n\n"
                f"WhatsApp opened — just click Send."
            )
        except Exception as e:
            return f"Failed to open WhatsApp: {e}"

    async def _app_launcher(self, app_name: str) -> str:
        """Open a Windows application by name."""
        import subprocess

        app_map = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "calc": "calc.exe",
            "explorer": "explorer.exe",
            "file explorer": "explorer.exe",
            "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "firefox": r"C:\Program Files\Mozilla Firefox\firefox.exe",
            "code": "code",
            "vscode": "code",
            "vs code": "code",
            "outlook": "outlook.exe",
            "teams": r"C:\Users\anubh\AppData\Local\Microsoft\Teams\current\Teams.exe",
            "slack": r"C:\Users\anubh\AppData\Local\slack\slack.exe",
            "paint": "mspaint.exe",
            "cmd": "cmd.exe",
            "powershell": "powershell.exe",
            "task manager": "taskmgr.exe",
            "settings": "ms-settings:",
            "snipping tool": "snippingtool.exe",
            "word": "winword.exe",
            "excel": "excel.exe",
            "powerpoint": "powerpnt.exe",
        }

        key = app_name.strip().lower()
        executable = app_map.get(key, app_name)

        try:
            if executable.startswith("ms-"):
                import os
                os.startfile(executable)
            else:
                subprocess.Popen(
                    executable,
                    shell=True,
                    creationflags=0x08000000,
                )
            return f"Launched {app_name} successfully."
        except Exception as e:
            return f"Failed to launch {app_name}: {e}"

    async def _clipboard_read(self) -> str:
        """Read the system clipboard contents."""
        import subprocess

        try:
            result = subprocess.run(
                ["powershell", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000,
            )
            content = result.stdout.strip()
            if not content:
                return "Clipboard is empty."
            if len(content) > 8000:
                return content[:8000] + f"\n\n... [truncated, {len(content)} chars total]"
            return f"Clipboard contents:\n{content}"
        except Exception as e:
            return f"Failed to read clipboard: {e}"

    async def _clipboard_write(self, text: str) -> str:
        """Write text to the system clipboard."""
        import subprocess

        try:
            subprocess.run(
                ["powershell", "-Command", f"Set-Clipboard -Value '{text}'"],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000,
            )
            return f"Copied to clipboard ({len(text)} chars)."
        except Exception as e:
            return f"Failed to write to clipboard: {e}"

    async def _pdf_reader(self, path: str) -> str:
        """Extract text from a PDF file."""
        try:
            from PyPDF2 import PdfReader

            reader = PdfReader(path)
            pages = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"--- Page {i + 1} ---\n{text}")

            if not pages:
                return "Could not extract any text from the PDF. It may be scanned/image-based."

            full_text = "\n\n".join(pages)
            if len(full_text) > 8000:
                return full_text[:8000] + f"\n\n... [truncated, {len(full_text)} chars total, {len(reader.pages)} pages]"
            return f"PDF ({len(reader.pages)} pages):\n\n{full_text}"
        except FileNotFoundError:
            return f"File not found: {path}"
        except Exception as e:
            return f"Failed to read PDF: {e}"

    async def _csv_reader(self, path: str, query: str = "") -> str:
        """Read and analyze a CSV or Excel file."""
        import csv
        import os

        ext = os.path.splitext(path)[1].lower()
        if ext in (".xlsx", ".xls"):
            return "Excel files (.xlsx/.xls) are not supported yet. Please convert to CSV first."

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f)
                rows = list(reader)

            if not rows:
                return "The CSV file is empty."

            headers = rows[0]
            data_rows = rows[1:]
            total = len(data_rows)

            result_lines = [
                f"CSV File: {os.path.basename(path)}",
                f"Columns ({len(headers)}): {', '.join(headers)}",
                f"Total rows: {total}",
                "",
            ]

            if query:
                query_lower = query.lower()
                matched = [r for r in data_rows if any(query_lower in cell.lower() for cell in r)]
                result_lines.append(f"Search '{query}': {len(matched)} matches")
                show_rows = matched[:20]
            else:
                show_rows = data_rows[:20]

            if show_rows:
                # Format as table
                col_widths = [len(h) for h in headers]
                for row in show_rows:
                    for i, cell in enumerate(row):
                        if i < len(col_widths):
                            col_widths[i] = max(col_widths[i], min(len(cell), 30))

                header_line = " | ".join(h.ljust(col_widths[i])[:30] for i, h in enumerate(headers))
                result_lines.append(header_line)
                result_lines.append("-" * len(header_line))
                for row in show_rows:
                    line = " | ".join(
                        (row[i] if i < len(row) else "").ljust(col_widths[i])[:30]
                        for i in range(len(headers))
                    )
                    result_lines.append(line)

                if (not query and total > 20) or (query and len(matched) > 20):
                    result_lines.append(f"\n... showing first 20 of {'matches' if query else 'rows'}")

            output = "\n".join(result_lines)
            if len(output) > 8000:
                return output[:8000] + "\n... [truncated]"
            return output
        except FileNotFoundError:
            return f"File not found: {path}"
        except Exception as e:
            return f"Failed to read CSV: {e}"

    async def _system_info(self) -> str:
        """Get system information: CPU, memory, disk, battery."""
        lines = []

        try:
            import psutil

            # CPU
            cpu_percent = psutil.cpu_percent(interval=1)
            cpu_count = psutil.cpu_count()
            lines.append(f"CPU: {cpu_percent}% usage ({cpu_count} cores)")

            # Memory
            mem = psutil.virtual_memory()
            lines.append(
                f"Memory: {mem.percent}% used "
                f"({mem.used // (1024**3):.1f} GB / {mem.total // (1024**3):.1f} GB)"
            )

            # Disk
            for part in psutil.disk_partitions():
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    lines.append(
                        f"Disk {part.device}: {usage.percent}% used "
                        f"({usage.used // (1024**3):.0f} GB / {usage.total // (1024**3):.0f} GB)"
                    )
                except PermissionError:
                    pass

            # Battery
            battery = psutil.sensors_battery()
            if battery:
                plug = "plugged in" if battery.power_plugged else "on battery"
                lines.append(f"Battery: {battery.percent}% ({plug})")

            # Uptime
            import time
            boot = psutil.boot_time()
            uptime_secs = int(time.time() - boot)
            hours, remainder = divmod(uptime_secs, 3600)
            mins, _ = divmod(remainder, 60)
            lines.append(f"Uptime: {hours}h {mins}m")

        except ImportError:
            # Fallback without psutil
            import subprocess
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory | Format-List"],
                capture_output=True, text=True, timeout=10,
                creationflags=0x08000000,
            )
            lines.append(result.stdout.strip() or "Could not retrieve system info (psutil not installed).")

        return "\n".join(lines)

    async def _screenshot(self, save_path: str = "") -> str:
        """Take a screenshot and save it."""
        import os
        import subprocess
        from datetime import datetime

        if not save_path:
            # Default to user's Screenshots folder
            screenshots_dir = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
            onedrive_dir = os.path.join(os.path.expanduser("~"), "OneDrive", "Pictures", "Screenshots")
            if os.path.isdir(onedrive_dir):
                screenshots_dir = onedrive_dir
            os.makedirs(screenshots_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = os.path.join(screenshots_dir, f"screenshot_{timestamp}.png")

        # Use PowerShell to take a screenshot
        ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
$screen = [System.Windows.Forms.Screen]::PrimaryScreen
$bitmap = New-Object System.Drawing.Bitmap($screen.Bounds.Width, $screen.Bounds.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($screen.Bounds.Location, [System.Drawing.Point]::Empty, $screen.Bounds.Size)
$bitmap.Save('{save_path}')
$graphics.Dispose()
$bitmap.Dispose()
"""
        try:
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True, text=True, timeout=10,
                creationflags=0x08000000,
            )
            if os.path.exists(save_path):
                return f"Screenshot saved to: {save_path}"
            else:
                return f"Screenshot may have failed. PowerShell output: {result.stderr or result.stdout}"
        except Exception as e:
            return f"Failed to take screenshot: {e}"

    async def _git_status(self, repo_path: str = "") -> str:
        """Get git status of a repository."""
        import subprocess
        import os

        if not repo_path:
            repo_path = os.path.join(os.path.expanduser("~"), "Downloads", "myai")

        if not os.path.isdir(repo_path):
            return f"Directory not found: {repo_path}"

        sections = []

        try:
            # git status
            result = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
                creationflags=0x08000000,
            )
            status = result.stdout.strip()
            sections.append(f"Status:\n{status or '(clean — no changes)'}")

            # git branch
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
                creationflags=0x08000000,
            )
            branch = result.stdout.strip()
            sections.insert(0, f"Branch: {branch}")

            # git log
            result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
                creationflags=0x08000000,
            )
            log = result.stdout.strip()
            if log:
                sections.append(f"Recent commits:\n{log}")

            # git diff --stat
            result = subprocess.run(
                ["git", "diff", "--stat"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
                creationflags=0x08000000,
            )
            diff = result.stdout.strip()
            if diff:
                sections.append(f"Unstaged changes:\n{diff}")

        except FileNotFoundError:
            return "Git is not installed or not in PATH."
        except Exception as e:
            return f"Failed to get git status: {e}"

        return "\n\n".join(sections)

    async def _url_summarizer(self, url: str) -> str:
        """Fetch a URL and return its text content for summarization."""
        import re

        try:
            import httpx
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                html = response.text
        except Exception as e:
            return f"Failed to fetch URL: {e}"

        # Strip HTML tags
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        # Decode HTML entities
        import html as html_mod
        text = html_mod.unescape(text)

        if not text:
            return "Could not extract any text from the page."

        if len(text) > 3000:
            text = text[:3000] + f"\n\n... [truncated, {len(text)} chars total]"

        return f"Content from {url}:\n\n{text}"

    async def _open_url(self, url: str) -> str:
        """Open a URL in the default browser."""
        import webbrowser

        try:
            webbrowser.open(url)
            return f"Opened {url} in your default browser."
        except Exception as e:
            return f"Failed to open URL: {e}"

    async def _type_in_app(self, app: str = "", text: str = "", hotkey: str = "") -> str:
        """Open an app and type text into it, or press hotkeys. Computer use."""
        import subprocess
        import time as _time

        try:
            import pyautogui
            pyautogui.PAUSE = 0.3
            pyautogui.FAILSAFE = True
        except ImportError:
            return "pyautogui not installed. Run: pip install pyautogui"

        try:
            # Step 1: Open the app if specified
            if app:
                app_map = {
                    "notepad": "notepad.exe",
                    "calculator": "calc.exe",
                    "paint": "mspaint.exe",
                    "wordpad": "wordpad.exe",
                    "cmd": "cmd.exe",
                    "powershell": "powershell.exe",
                    "explorer": "explorer.exe",
                }
                exe = app_map.get(app.lower(), app)
                subprocess.Popen(exe, shell=True)
                _time.sleep(1.5)  # Wait for app to open

            # Step 2: Type text if provided
            if text:
                # Use pyperclip/clipboard to paste (faster and handles special chars)
                import subprocess as sp
                sp.run(
                    ["powershell", "-Command", f"Set-Clipboard -Value '{text.replace(chr(39), chr(39)+chr(39))}'"],
                    capture_output=True, timeout=5,
                    creationflags=0x08000000,
                )
                _time.sleep(0.3)
                pyautogui.hotkey("ctrl", "v")
                result = f"Typed text into {app or 'active window'}."

            # Step 3: Press hotkey if specified (e.g., "ctrl+s", "alt+f4")
            elif hotkey:
                keys = [k.strip() for k in hotkey.split("+")]
                pyautogui.hotkey(*keys)
                result = f"Pressed {hotkey}."

            else:
                result = f"App '{app}' opened. No text or hotkey specified."

            return result

        except Exception as e:
            return f"Failed: {e}"

    async def _open_file(self, path: str) -> str:
        """Open a file by path, name, or description. Searches common folders if not a full path."""
        import os
        from pathlib import Path

        # If it's a full path and exists, open directly
        if os.path.isabs(path) and os.path.exists(path):
            os.startfile(path)
            return f"Opened {Path(path).name}"

        # Otherwise, search common folders for a matching file
        home = Path.home()
        search_dirs = [
            home / "Downloads",
            home / "OneDrive" / "Desktop",
            home / "Desktop",
            home / "OneDrive" / "Documents",
            home / "Documents",
            home / "OneDrive" / "Pictures",
            home / "Downloads" / "myai",
            home / "Downloads" / "openclaw-transfer",
        ]

        # Split query into keywords for flexible matching
        query_lower = path.lower()
        noise_words = {"the", "my", "latest", "new", "this", "that", "recent", "last", "a", "an", "file", "document", "doc"}
        keywords = [w for w in query_lower.replace("_", " ").replace("-", " ").split() if len(w) > 2 and w not in noise_words]
        # Also try the whole query stripped
        query_stripped = query_lower.replace(" ", "").replace("_", "").replace("-", "")
        matches = []

        for folder in search_dirs:
            if not folder.exists():
                continue
            try:
                entries = list(folder.iterdir())
            except PermissionError:
                continue
            for f in entries:
                if not f.is_file():
                    continue
                fname = f.name.lower()
                fname_stripped = fname.replace(" ", "").replace("_", "").replace("-", "")
                # Match by: any keyword in filename, or whole query in filename
                if query_stripped in fname_stripped or fname_stripped in query_stripped:
                    matches.append(f)
                elif keywords and all(kw in fname_stripped for kw in keywords):
                    matches.append(f)
                elif any(kw in fname_stripped for kw in keywords if len(kw) > 3):
                    matches.append(f)

        if not matches:
            return f"Could not find a file matching '{path}'. Try providing the full path."

        if len(matches) == 1:
            os.startfile(str(matches[0]))
            return f"Opened {matches[0].name} from {matches[0].parent}"

        # Multiple matches — pick the most recent
        matches.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        os.startfile(str(matches[0]))
        other_names = ", ".join(m.name for m in matches[1:4])
        return f"Opened {matches[0].name} from {matches[0].parent}. Also found: {other_names}"

    @staticmethod
    def parse_tool_call(text: str) -> dict | None:
        """Extract a tool call JSON from the model's response."""
        import re

        # 1. Look for ```tool ... ``` blocks
        pattern = r"```tool\s*\n?\s*(\{.*?\})\s*\n?\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                return ToolRegistry._normalize_tool_call(parsed)
            except json.JSONDecodeError:
                pass

        # 2. Look for ```json ... ``` or ``` ... ``` blocks containing tool calls
        pattern_code = r"```(?:json)?\s*\n?\s*(\{.*?\})\s*\n?\s*```"
        for m in re.finditer(pattern_code, text, re.DOTALL):
            try:
                parsed = json.loads(m.group(1))
                if "name" in parsed and ("arguments" in parsed or "parameters" in parsed):
                    return ToolRegistry._normalize_tool_call(parsed)
            except json.JSONDecodeError:
                continue

        # 3. Try bare JSON with "name" key
        pattern3 = r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"(?:arguments|parameters)"\s*:\s*\{.*?\}\s*\}'
        match3 = re.search(pattern3, text, re.DOTALL)
        if match3:
            try:
                parsed = json.loads(match3.group(0))
                return ToolRegistry._normalize_tool_call(parsed)
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _normalize_tool_call(parsed: dict) -> dict:
        """Normalize tool call dict — handle 'parameters' vs 'arguments' key."""
        if "parameters" in parsed and "arguments" not in parsed:
            parsed["arguments"] = parsed.pop("parameters")
        return parsed
