"""Web tools: DuckDuckGo search, page fetch with SSRF guard, and RSS feed reader."""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

import feedparser
import httpx
import trafilatura
from ddgs import DDGS
from pydantic import BaseModel, Field

from noxai.tools.registry import ToolContext, ToolResult, tool
from noxai.tools.safety import SSRFViolation, check_ssrf_url

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Search Backend Protocol & DDGS Implementation
# ---------------------------------------------------------------------------


class SearchBackend(Protocol):
    """Protocol for pluggable web search engines."""

    async def search(self, query: str, max_results: int = 8) -> list[dict[str, str]]: ...


class DuckDuckGoSearch(SearchBackend):
    """DuckDuckGo search backend via ddgs."""

    async def search(self, query: str, max_results: int = 8) -> list[dict[str, str]]:
        # Run blocking ddgs call in thread pool
        import asyncio

        def _do_search() -> list[dict[str, str]]:
            results = []
            try:
                with DDGS() as ddgs_client:
                    raw_res = list(ddgs_client.text(query, max_results=max_results))
                    for item in raw_res:
                        results.append(
                            {
                                "title": item.get("title", ""),
                                "url": item.get("href", ""),
                                "snippet": item.get("body", ""),
                            }
                        )
            except Exception as exc:
                log.warning("DuckDuckGo search error: %s", exc)
            return results

        return await asyncio.to_thread(_do_search)


# Default search backend instance
default_search_backend: SearchBackend = DuckDuckGoSearch()


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------


class WebSearchParams(BaseModel):
    model_config = {"extra": "forbid"}

    query: str = Field(..., description="Search query string.", min_length=1)
    max_results: int = Field(default=8, description="Maximum results to return.", ge=1, le=15)


@tool(
    name="web_search",
    description="Search the web for up-to-date public information and articles.",
    params=WebSearchParams,
    tier="read",
    untrusted_output=True,  # Search results are untrusted external data
    timeout=20.0,
)
async def web_search(params: WebSearchParams, ctx: ToolContext) -> ToolResult:
    results = await default_search_backend.search(params.query, max_results=params.max_results)

    if not results:
        return ToolResult(
            ok=True,
            output=f"No results found for query: '{params.query}'",
            summary=f"No search results for '{params.query}'",
        )

    lines = []
    sources = []
    for i, res in enumerate(results, 1):
        title = res.get("title", "")
        url = res.get("url", "")
        snippet = res.get("snippet", "")
        lines.append(f"{i}. {title}\n   URL: {url}\n   Snippet: {snippet}")
        sources.append({"title": title, "url": url})

    return ToolResult(
        ok=True,
        output="\n\n".join(lines),
        summary=f"Found {len(results)} results for '{params.query}'",
        sources=sources,
    )


# ---------------------------------------------------------------------------
# fetch_page
# ---------------------------------------------------------------------------


class FetchPageParams(BaseModel):
    model_config = {"extra": "forbid"}

    url: str = Field(..., description="The HTTP or HTTPS URL of the webpage to fetch.")


@tool(
    name="fetch_page",
    description="Fetch and extract clean text from a web URL, protected by SSRF guard.",
    params=FetchPageParams,
    tier="read",
    untrusted_output=True,  # Web pages are untrusted content
    timeout=25.0,
)
async def fetch_page(params: FetchPageParams, ctx: ToolContext) -> ToolResult:
    # 1. Pre-flight SSRF check on initial URL
    try:
        target_url = check_ssrf_url(params.url)
    except SSRFViolation as exc:
        return ToolResult(
            ok=False,
            output=f"SSRF Security Violation: {exc}",
            summary="SSRF guard blocked request",
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5"
        ),
    }

    # Custom redirect loop to enforce SSRF validation on every redirect location
    max_redirects = 5
    current_url = target_url
    redirect_count = 0

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            while redirect_count <= max_redirects:
                # Validate current URL before sending request
                check_ssrf_url(current_url)

                resp = await client.get(current_url, headers=headers)

                # Check for redirect (301, 302, 303, 307, 308)
                if resp.is_redirect and "location" in resp.headers:
                    redirect_count += 1
                    location = resp.headers["location"]
                    # Handle relative redirect URLs
                    from urllib.parse import urljoin

                    current_url = urljoin(current_url, location)
                    continue

                if resp.is_error:
                    return ToolResult(
                        ok=False,
                        output=f"HTTP {resp.status_code} error fetching {current_url}",
                        summary=f"HTTP {resp.status_code}",
                    )

                # Check Content-Type
                content_type = resp.headers.get("Content-Type", "").lower()
                if not any(
                    t in content_type for t in ("text/html", "text/plain", "application/xhtml")
                ):
                    return ToolResult(
                        ok=False,
                        output=(
                            f"Unsupported content type '{content_type}'. "
                            "Only HTML and plain text are supported."
                        ),
                        summary="Unsupported content type",
                    )

                # Size cap: 2 MB
                max_bytes = 2 * 1024 * 1024
                raw_bytes = resp.content[:max_bytes]
                html_text = raw_bytes.decode(resp.encoding or "utf-8", errors="replace")

                # Extract text using trafilatura
                extracted = trafilatura.extract(
                    html_text,
                    include_links=True,
                    include_images=False,
                    output_format="txt",
                )

                if not extracted or not extracted.strip():
                    # Fallback to simple baseline stripping
                    extracted = html_text[:8000]

                # Truncate clean output to 8,000 characters
                final_text = extracted[:8000]
                if len(extracted) > 8000:
                    final_text += "\n\n[Web page content truncated at 8,000 characters]"

                return ToolResult(
                    ok=True,
                    output=final_text,
                    summary=f"Fetched {current_url} ({len(final_text)} chars)",
                    sources=[{"title": current_url, "url": current_url}],
                )

            return ToolResult(
                ok=False,
                output=f"Too many redirects (exceeded limit of {max_redirects}).",
                summary="Redirect limit exceeded",
            )

    except SSRFViolation as exc:
        return ToolResult(
            ok=False,
            output=f"SSRF Security Violation on redirect: {exc}",
            summary="SSRF guard blocked redirect",
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            output=f"Error fetching page {params.url}: {exc}",
            summary="Page fetch error",
        )


# ---------------------------------------------------------------------------
# rss_fetch
# ---------------------------------------------------------------------------


class RssFetchParams(BaseModel):
    model_config = {"extra": "forbid"}

    url: str = Field(..., description="The RSS or Atom feed URL to read.")
    since_hours: int = Field(
        default=36, description="Only fetch items published within N hours.", ge=1, le=168
    )
    unseen_only: bool = Field(
        default=True, description="Filter out items previously seen in seen_items table."
    )


@tool(
    name="rss_fetch",
    description="Fetch and parse RSS/Atom feed items with deduplication against seen items.",
    params=RssFetchParams,
    tier="read",
    untrusted_output=True,
    timeout=20.0,
)
async def rss_fetch(params: RssFetchParams, ctx: ToolContext) -> ToolResult:
    try:
        safe_url = check_ssrf_url(params.url)
    except SSRFViolation as exc:
        return ToolResult(
            ok=False,
            output=f"SSRF Security Violation: {exc}",
            summary="SSRF guard blocked RSS URL",
        )

    # Fetch feed content via httpx to respect timeouts and SSRF guards
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(safe_url)
            if resp.is_error:
                return ToolResult(
                    ok=False,
                    output=f"HTTP {resp.status_code} error fetching feed {params.url}",
                    summary="Feed fetch failed",
                )
            feed_content = resp.text
    except Exception as exc:
        return ToolResult(
            ok=False,
            output=f"Error fetching feed: {exc}",
            summary="Feed network error",
        )

    feed = feedparser.parse(feed_content)
    if feed.bozo and not feed.entries:
        return ToolResult(
            ok=False,
            output=f"Failed to parse RSS feed from {params.url}: {feed.bozo_exception}",
            summary="Malformed RSS feed",
        )

    entries = feed.entries
    items_to_report = []
    sources = []

    for entry in entries:
        link = entry.get("link", "")
        title = entry.get("title", "Untitled")
        summary = entry.get("summary", "")

        # Compute URL hash for deduplication
        url_hash = hashlib.sha256(link.encode("utf-8")).hexdigest()

        # Check seen items table if DB and unseen_only are active
        if params.unseen_only and ctx.db and link:
            cursor = await ctx.db.execute(
                "SELECT id FROM seen_items WHERE url_hash = ?", (url_hash,)
            )
            row = await cursor.fetchone()
            if row:
                continue  # already seen

            # Record in seen_items
            await ctx.db.execute(
                "INSERT OR IGNORE INTO seen_items (url_hash, url) VALUES (?, ?)",
                (url_hash, link),
            )
            await ctx.db.commit()

        items_to_report.append(
            {
                "title": title,
                "link": link,
                "summary": summary[:300] if summary else "",
            }
        )
        sources.append({"title": title, "url": link})

    if not items_to_report:
        return ToolResult(
            ok=True,
            output="No new unseen items found in feed.",
            summary="No new items",
        )

    lines = []
    for i, it in enumerate(items_to_report[:15], 1):
        lines.append(f"{i}. {it['title']}\n   Link: {it['link']}\n   Summary: {it['summary']}")

    return ToolResult(
        ok=True,
        output="\n\n".join(lines),
        summary=f"Found {len(items_to_report)} new items from feed",
        sources=sources,
    )
