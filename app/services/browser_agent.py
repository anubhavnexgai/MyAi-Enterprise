"""Browser automation for MyAi using Playwright."""
from __future__ import annotations
import logging
import re

logger = logging.getLogger(__name__)


class BrowserAgent:
    """Controls a browser to perform web tasks and return content."""

    async def execute_task(self, task: str) -> str:
        """Execute a browser task and return extracted content."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return "Browser automation requires playwright. Run: pip install playwright && playwright install chromium"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                task_lower = task.lower()

                # Determine URL
                url = None
                query = None

                # Check for explicit URL
                url_match = re.search(r'(https?://\S+)', task)
                if url_match:
                    url = url_match.group(1)

                # Search task
                elif "search" in task_lower:
                    query = task_lower
                    for remove in ["search for", "search", "on google", "google", "browse", "and tell me", "and summarize"]:
                        query = query.replace(remove, "")
                    query = query.strip()
                    url = f"https://www.google.com/search?q={query.replace(' ', '+')}"

                # "go to X" or "open X" or just a site name
                elif any(kw in task_lower for kw in ["go to", "open", "navigate", "browse to", "visit"]):
                    site = task_lower
                    for remove in ["go to", "open", "navigate to", "browse to", "visit", "browse",
                                   "and tell me", "and summarize", "what's", "what is", "trending",
                                   "in the browser", "the browser"]:
                        site = site.replace(remove, "")
                    site = site.strip().rstrip(".")

                    if "." in site:
                        url = f"https://{site}" if not site.startswith("http") else site
                    else:
                        url = f"https://{site.replace(' ', '')}.com"

                else:
                    # Default: search Google
                    query = task_lower
                    url = f"https://www.google.com/search?q={task_lower.replace(' ', '+')}"

                # Navigate
                await page.goto(url, timeout=20000, wait_until="domcontentloaded")
                title = await page.title()

                # Extract content
                content = ""

                if query and "google.com/search" in url:
                    # Google search — extract results
                    try:
                        await page.wait_for_selector("h3", timeout=5000)
                        results = await page.query_selector_all("h3")
                        snippets = []
                        for r in results[:8]:
                            t = await r.text_content()
                            if t and len(t) > 5:
                                snippets.append(t)
                        if snippets:
                            content = f"Google search results for '{query}':\n"
                            content += "\n".join(f"  {i+1}. {s}" for i, s in enumerate(snippets))
                    except Exception:
                        content = f"Searched Google for '{query}' — page loaded."
                else:
                    # Regular page — extract main text
                    try:
                        body_text = await page.inner_text("body")
                        # Clean up — remove excessive whitespace
                        lines = [l.strip() for l in body_text.split("\n") if l.strip() and len(l.strip()) > 3]
                        # Take first 2000 chars of meaningful content
                        content = "\n".join(lines)[:2000]
                    except Exception:
                        content = f"Page loaded but couldn't extract text."

                await browser.close()

                result = f"**{title}** ({url})\n\n{content}"
                # Truncate if too long
                if len(result) > 3000:
                    result = result[:3000] + "\n\n... (truncated)"
                return result

        except Exception as e:
            return f"Browser error: {str(e)[:300]}"
