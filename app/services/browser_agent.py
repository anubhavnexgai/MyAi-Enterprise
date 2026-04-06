"""Browser automation for MyAi using browser-use library."""
from __future__ import annotations
import logging
import asyncio

logger = logging.getLogger(__name__)


class BrowserAgent:
    """Controls a browser to perform web tasks."""

    async def execute_task(self, task: str) -> str:
        """Execute a browser task described in natural language."""
        try:
            from browser_use import Agent as BUAgent
            from langchain_community.llms import Ollama

            # Try using browser-use with local Ollama
            # If browser-use needs specific LLM, fall back to simpler approach
            pass
        except ImportError:
            pass

        # Fallback: use Playwright directly for simple tasks
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=False)
                page = await browser.new_page()

                # Parse simple commands
                task_lower = task.lower()

                if "go to" in task_lower or "open" in task_lower or "navigate" in task_lower:
                    # Extract URL
                    import re
                    url_match = re.search(r'(https?://\S+)', task)
                    if url_match:
                        url = url_match.group(1)
                    else:
                        # Try to construct URL from site name
                        words = task_lower.replace("go to", "").replace("open", "").replace("navigate to", "").strip()
                        url = f"https://{words.replace(' ', '')}.com" if "." not in words else f"https://{words}"

                    await page.goto(url, timeout=15000)
                    title = await page.title()

                    # Take screenshot
                    import os, tempfile
                    screenshot_path = os.path.join(tempfile.gettempdir(), "myai_browser_screenshot.png")
                    await page.screenshot(path=screenshot_path)

                    await browser.close()
                    return f"Opened {url} — Page title: {title}"

                elif "search" in task_lower:
                    query = task_lower.replace("search for", "").replace("search", "").replace("on google", "").strip()
                    await page.goto(f"https://www.google.com/search?q={query}", timeout=15000)

                    # Extract top results
                    await page.wait_for_selector("h3", timeout=5000)
                    results = await page.query_selector_all("h3")
                    titles = []
                    for r in results[:5]:
                        t = await r.text_content()
                        if t:
                            titles.append(t)

                    await browser.close()
                    if titles:
                        return f"Search results for '{query}':\n" + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(titles))
                    return f"Searched for '{query}' on Google."

                else:
                    await browser.close()
                    return f"Browser task not understood: {task}. Try 'go to [url]' or 'search for [query]'."

        except ImportError:
            return "Browser automation requires playwright. Run: pip install playwright && playwright install chromium"
        except Exception as e:
            return f"Browser error: {str(e)[:200]}"
