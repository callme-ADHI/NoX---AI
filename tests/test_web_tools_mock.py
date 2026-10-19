"""Unit tests for web tools with mock transports (Phase E7.3)."""

from __future__ import annotations

from unittest.mock import patch

import aiosqlite
import httpx
import pytest

from noxai.config import get_config
from noxai.tools.builtin.web import (
    FetchPageParams,
    RssFetchParams,
    SearchBackend,
    WebSearchParams,
    fetch_page,
    rss_fetch,
    web_search,
)
from noxai.tools.registry import ToolContext
from noxai.tools.safety import SSRFViolation

_orig_async_client = httpx.AsyncClient


def _make_mock_client_factory(handler):
    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _orig_async_client(*args, **kwargs)

    return factory


# ============================================================================
# WEB SEARCH TESTS
# ============================================================================


class MockSearchBackend(SearchBackend):
    def __init__(
        self, results: list[dict[str, str]] | None = None, raise_exc: Exception | None = None
    ) -> None:
        self.results = results if results is not None else []
        self.raise_exc = raise_exc

    async def search(self, query: str, max_results: int = 8) -> list[dict[str, str]]:
        if self.raise_exc:
            raise self.raise_exc
        return self.results[:max_results]


@pytest.mark.asyncio
async def test_web_search_success_and_normalization() -> None:
    """web_search normalizes output and sources from search backend."""
    mock_results = [
        {"title": "Result 1", "url": "https://example.com/1", "snippet": "Snippet 1"},
        {"title": "Result 2", "url": "https://example.com/2", "snippet": "Snippet 2"},
    ]
    with patch(
        "noxai.tools.builtin.web.default_search_backend", MockSearchBackend(results=mock_results)
    ):
        ctx = ToolContext(run_id="test_run", config=get_config())
        res = await web_search(WebSearchParams(query="test query", max_results=5), ctx)
        assert res.ok is True
        assert "Result 1" in res.output
        assert "https://example.com/1" in res.output
        assert "Found 2 results" in res.summary
        assert len(res.sources or []) == 2
        assert res.sources[0]["url"] == "https://example.com/1"


@pytest.mark.asyncio
async def test_web_search_empty_results() -> None:
    """web_search handles zero results gracefully."""
    with patch("noxai.tools.builtin.web.default_search_backend", MockSearchBackend(results=[])):
        ctx = ToolContext(run_id="test_run", config=get_config())
        res = await web_search(WebSearchParams(query="empty query"), ctx)
        assert res.ok is True
        assert "No results found" in res.output


# ============================================================================
# FETCH PAGE TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_fetch_page_ssrf_preflight_blocked() -> None:
    """fetch_page pre-flight blocks private IPs."""
    ctx = ToolContext(run_id="test_run", config=get_config())
    # 127.0.0.1 is blocked by check_ssrf_url
    res = await fetch_page(FetchPageParams(url="http://127.0.0.1:8080/secret"), ctx)
    assert res.ok is False
    assert "SSRF Security Violation" in res.output


@pytest.mark.asyncio
async def test_fetch_page_success_and_trafilatura_extraction() -> None:
    """fetch_page extracts article text and returns clean output."""
    html_content = (
        "<!DOCTYPE html><html><head><title>Test Page</title></head>"
        "<body><article><h1>Main Article</h1>"
        "<p>This is the important body content of the article.</p></article></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=html_content,
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    ctx = ToolContext(run_id="test_run", config=get_config())
    with patch("noxai.tools.builtin.web.check_ssrf_url", side_effect=lambda u: u):
        with patch(
            "noxai.tools.builtin.web.httpx.AsyncClient",
            side_effect=_make_mock_client_factory(handler),
        ):
            res = await fetch_page(FetchPageParams(url="https://example.com/article"), ctx)
            assert res.ok is True
            assert "Main Article" in res.output or "body content" in res.output
            assert "https://example.com/article" in res.summary


@pytest.mark.asyncio
async def test_fetch_page_unsupported_content_type() -> None:
    """fetch_page rejects non-HTML/non-text content types (e.g. PDF or binary)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"%PDF-1.4...", headers={"Content-Type": "application/pdf"}
        )

    ctx = ToolContext(run_id="test_run", config=get_config())
    with patch("noxai.tools.builtin.web.check_ssrf_url", side_effect=lambda u: u):
        with patch(
            "noxai.tools.builtin.web.httpx.AsyncClient",
            side_effect=_make_mock_client_factory(handler),
        ):
            res = await fetch_page(FetchPageParams(url="https://example.com/doc.pdf"), ctx)
            assert res.ok is False
            assert "Unsupported content type 'application/pdf'" in res.output


@pytest.mark.asyncio
async def test_fetch_page_http_error() -> None:
    """fetch_page handles HTTP 404/500 errors."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    ctx = ToolContext(run_id="test_run", config=get_config())
    with patch("noxai.tools.builtin.web.check_ssrf_url", side_effect=lambda u: u):
        with patch(
            "noxai.tools.builtin.web.httpx.AsyncClient",
            side_effect=_make_mock_client_factory(handler),
        ):
            res = await fetch_page(FetchPageParams(url="https://example.com/missing"), ctx)
            assert res.ok is False
            assert "HTTP 404 error" in res.output


@pytest.mark.asyncio
async def test_fetch_page_redirect_limit_exceeded() -> None:
    """fetch_page enforces max_redirects (5)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://example.com/redirect-loop"})

    ctx = ToolContext(run_id="test_run", config=get_config())
    with patch("noxai.tools.builtin.web.check_ssrf_url", side_effect=lambda u: u):
        with patch(
            "noxai.tools.builtin.web.httpx.AsyncClient",
            side_effect=_make_mock_client_factory(handler),
        ):
            res = await fetch_page(FetchPageParams(url="https://example.com/start"), ctx)
            assert res.ok is False
            assert "Too many redirects" in res.output


@pytest.mark.asyncio
async def test_fetch_page_ssrf_on_redirect() -> None:
    """fetch_page checks SSRF on each redirect target URL."""

    def mock_check_ssrf(url: str) -> str:
        if "evil" in url:
            raise SSRFViolation("Forbidden internal redirect")
        return url

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.internal/metadata"})

    ctx = ToolContext(run_id="test_run", config=get_config())
    with patch("noxai.tools.builtin.web.check_ssrf_url", side_effect=mock_check_ssrf):
        with patch(
            "noxai.tools.builtin.web.httpx.AsyncClient",
            side_effect=_make_mock_client_factory(handler),
        ):
            res = await fetch_page(FetchPageParams(url="https://example.com/start"), ctx)
            assert res.ok is False
            assert "SSRF Security Violation on redirect" in res.output


@pytest.mark.asyncio
async def test_fetch_page_truncation_to_8000_chars() -> None:
    """fetch_page truncates large page content to 8,000 characters with notice."""
    huge_text = "<p>" + ("A" * 10000) + "</p>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=f"<html><body>{huge_text}</body></html>",
            headers={"Content-Type": "text/html"},
        )

    ctx = ToolContext(run_id="test_run", config=get_config())
    with patch("noxai.tools.builtin.web.check_ssrf_url", side_effect=lambda u: u):
        with patch(
            "noxai.tools.builtin.web.httpx.AsyncClient",
            side_effect=_make_mock_client_factory(handler),
        ):
            res = await fetch_page(FetchPageParams(url="https://example.com/huge"), ctx)
            assert res.ok is True
            assert "[Web page content truncated at 8,000 characters]" in res.output


# ============================================================================
# RSS FETCH TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_rss_fetch_parsing_and_seen_items_dedup() -> None:
    """rss_fetch parses feed entries, deduplicates against seen_items in DB."""
    db = await aiosqlite.connect(":memory:")
    await db.execute(
        """
        CREATE TABLE seen_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash TEXT NOT NULL UNIQUE,
            url TEXT NOT NULL,
            first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    await db.commit()

    sample_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Tech Feed</title>
        <link>https://example.com/feed</link>
        <item>
          <title>Article One</title>
          <link>https://example.com/article-1</link>
          <description>Summary of article one.</description>
        </item>
        <item>
          <title>Article Two</title>
          <link>https://example.com/article-2</link>
          <description>Summary of article two.</description>
        </item>
      </channel>
    </rss>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sample_rss, headers={"Content-Type": "application/rss+xml"})

    try:
        ctx = ToolContext(run_id="test_run", config=get_config(), db=db)
        with patch("noxai.tools.builtin.web.check_ssrf_url", side_effect=lambda u: u):
            with patch(
                "noxai.tools.builtin.web.httpx.AsyncClient",
                side_effect=_make_mock_client_factory(handler),
            ):
                # 1. First fetch returns both items
                res1 = await rss_fetch(
                    RssFetchParams(url="https://example.com/rss", unseen_only=True), ctx
                )
                assert res1.ok is True
                assert "Article One" in res1.output
                assert "Article Two" in res1.output
                assert len(res1.sources or []) == 2

                # 2. Second fetch with unseen_only filters both out
                res2 = await rss_fetch(
                    RssFetchParams(url="https://example.com/rss", unseen_only=True), ctx
                )
                assert res2.ok is True
                assert "No new unseen items found" in res2.output
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rss_fetch_malformed_feed() -> None:
    """rss_fetch handles malformed / non-XML feed gracefully."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="This is not XML at all", headers={"Content-Type": "text/plain"}
        )

    ctx = ToolContext(run_id="test_run", config=get_config())
    with patch("noxai.tools.builtin.web.check_ssrf_url", side_effect=lambda u: u):
        with patch(
            "noxai.tools.builtin.web.httpx.AsyncClient",
            side_effect=_make_mock_client_factory(handler),
        ):
            res = await rss_fetch(RssFetchParams(url="https://example.com/broken_feed"), ctx)
            assert res.ok is False
            assert "Failed to parse RSS feed" in res.output


@pytest.mark.asyncio
async def test_rss_fetch_network_error_and_ssrf() -> None:
    """rss_fetch handles network errors and SSRF blocks."""
    ctx = ToolContext(run_id="test_run", config=get_config())
    # SSRF block
    res_ssrf = await rss_fetch(RssFetchParams(url="http://169.254.169.254/latest/meta-data"), ctx)
    assert res_ssrf.ok is False
    assert "SSRF Security Violation" in res_ssrf.output

    # HTTP 500 error
    def err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Feed Server Down")

    with patch("noxai.tools.builtin.web.check_ssrf_url", side_effect=lambda u: u):
        with patch(
            "noxai.tools.builtin.web.httpx.AsyncClient",
            side_effect=_make_mock_client_factory(err_handler),
        ):
            res_err = await rss_fetch(RssFetchParams(url="https://example.com/down"), ctx)
            assert res_err.ok is False
            assert "HTTP 500 error fetching feed" in res_err.output
