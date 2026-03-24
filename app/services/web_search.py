from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class SearchResult:
    def __init__(self, title: str, url: str, snippet: str):
        self.title = title
        self.url = url
        self.snippet = snippet

    def __str__(self):
        return f"**{self.title}**\n{self.snippet}\nSource: {self.url}"


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        ...


class DuckDuckGoProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append(
                        SearchResult(
                            title=r.get("title", ""),
                            url=r.get("href", ""),
                            snippet=r.get("body", ""),
                        )
                    )
            return results
        except Exception as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            return []


class TavilyProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not settings.tavily_api_key:
            logger.warning("Tavily API key not set, falling back to empty results")
            return []

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": settings.tavily_api_key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                    },
                )
                r.raise_for_status()
                data = r.json()

            return [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                )
                for item in data.get("results", [])
            ]
        except Exception as e:
            logger.error(f"Tavily search failed: {e}")
            return []


class WebSearchService:
    def __init__(self):
        self.enabled = True  # On by default
        self._provider: SearchProvider | None = None

    @property
    def provider(self) -> SearchProvider:
        if self._provider is None:
            if settings.search_provider == "tavily" and settings.tavily_api_key:
                self._provider = TavilyProvider()
            else:
                self._provider = DuckDuckGoProvider()
        return self._provider

    async def search(self, query: str, max_results: int = 5) -> str:
        if not self.enabled:
            return (
                "Web search is currently disabled. "
                "Use `/search on` to enable it."
            )

        results = await self.provider.search(query, max_results)
        if not results:
            return f"No results found for: {query}"

        formatted = [f"**Web Search Results for: {query}**\n"]
        for i, r in enumerate(results, 1):
            formatted.append(f"{i}. {r}")

        return "\n\n".join(formatted)

    def toggle(self, on: bool):
        self.enabled = on
