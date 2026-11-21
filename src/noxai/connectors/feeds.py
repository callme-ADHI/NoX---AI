"""Public feeds connector for NoX supporting RSS/Atom, Bluesky, Mastodon, and Reddit.

Features:
- RSS / Atom feed ingestion via feedparser.
- Bluesky public posts via public unauthenticated API.
- Mastodon public timeline ingestion.
- Reddit RSS feeds.
- Input sanitization before local persistence in social_items.
- Daily social digest compilation.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from noxai.connectors.base import BaseConnector
from noxai.db import get_db
from noxai.security.guardrails import InputScanner

log = logging.getLogger(__name__)


class FeedsConnector(BaseConnector):
    """Connector for public decentralized and syndicated feeds."""

    def __init__(
        self,
        name: str = "feeds",
        config: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            name=name,
            connector_type="feeds",
            privacy_class="local_only",
            config=config or {},
            rate_limit_rpm=60,
        )
        self.rss_urls: list[str] = self.config.get("rss_urls", [])
        self.bluesky_handles: list[str] = self.config.get("bluesky_handles", [])
        self.mastodon_instances: list[str] = self.config.get("mastodon_instances", [])
        self.subreddits: list[str] = self.config.get("subreddits", [])
        self._custom_transport = transport

    def _get_client(self) -> httpx.AsyncClient:
        headers = {"User-Agent": "NoX-Assistant/1.0 (Personal AI Feed Reader)"}
        return httpx.AsyncClient(
            transport=self._custom_transport,
            headers=headers,
            timeout=30.0,
        )

    async def auth(self) -> bool:
        return True

    async def health(self) -> dict[str, Any]:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT health_status, health_reason, last_sync_at, last_error
                FROM connectors WHERE name = ?
                """,
                (self.name,),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return {
                        "status": row[0],
                        "reason": row[1] or "",
                        "last_sync_at": row[2],
                        "last_error": row[3],
                    }
        return {"status": "ok", "reason": "Feeds ready", "last_sync_at": None}

    def _clean_html(self, raw_html: str) -> str:
        clean = re.sub(r"<[^>]+>", " ", raw_html)
        clean = re.sub(r"\s+", " ", clean).strip()
        return InputScanner.scan(clean).sanitized_text

    async def _fetch_rss_feed(self, url: str) -> list[dict[str, Any]]:
        items = []
        try:
            async with self._get_client() as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    feed = feedparser.parse(resp.text)
                    feed_title = feed.feed.get("title", url)
                    for entry in feed.entries[:15]:
                        link = entry.get("link", "")
                        title = entry.get("title", "")
                        summary = entry.get("summary", "") or entry.get("description", "")
                        author = entry.get("author", feed_title)
                        pub = entry.get("published", "") or datetime.now(UTC).isoformat()
                        clean_content = self._clean_html(summary)
                        items.append(
                            {
                                "id": f"rss_{abs(hash(link or title)) & 0xFFFFFFFF:x}",
                                "source": "rss",
                                "channel_or_feed": feed_title,
                                "author": author,
                                "title": title,
                                "content": clean_content[:2048],
                                "url": link,
                                "published_at": pub,
                            }
                        )
        except Exception as exc:
            log.warning("rss_fetch_error url=%s error=%s", url, exc)
        return items

    async def _fetch_bluesky_handle(self, handle: str) -> list[dict[str, Any]]:
        items = []
        try:
            url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={handle}&limit=10"
            async with self._get_client() as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    feed = resp.json().get("feed", [])
                    for post_wrap in feed:
                        post = post_wrap.get("post", {})
                        record = post.get("record", {})
                        text = record.get("text", "")
                        uri = post.get("uri", "")
                        created_at = record.get("createdAt", datetime.now(UTC).isoformat())
                        clean_content = self._clean_html(text)
                        items.append(
                            {
                                "id": f"bsky_{abs(hash(uri or text)) & 0xFFFFFFFF:x}",
                                "source": "bluesky",
                                "channel_or_feed": f"@{handle}",
                                "author": handle,
                                "title": None,
                                "content": clean_content[:2048],
                                "url": uri,
                                "published_at": created_at,
                            }
                        )
        except Exception as exc:
            log.warning("bluesky_fetch_error handle=%s error=%s", handle, exc)
        return items

    async def _fetch_mastodon_instance(self, instance: str) -> list[dict[str, Any]]:
        items = []
        try:
            url = f"https://{instance}/api/v1/timelines/public?limit=15&local=true"
            async with self._get_client() as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    toots = resp.json()
                    for toot in toots:
                        content = toot.get("content", "")
                        toot_url = toot.get("url", "")
                        author = toot.get("account", {}).get("username", instance)
                        created_at = toot.get("created_at", datetime.now(UTC).isoformat())
                        clean_content = self._clean_html(content)
                        m_hash = abs(hash(toot_url or clean_content)) & 0xFFFFFFFF
                        items.append(
                            {
                                "id": f"masto_{m_hash:x}",
                                "source": "mastodon",
                                "channel_or_feed": instance,
                                "author": author,
                                "title": None,
                                "content": clean_content[:2048],
                                "url": toot_url,
                                "published_at": created_at,
                            }
                        )
        except Exception as exc:
            log.warning("mastodon_fetch_error instance=%s error=%s", instance, exc)
        return items

    async def _fetch_reddit_subreddit(self, subreddit: str) -> list[dict[str, Any]]:
        url = f"https://www.reddit.com/r/{subreddit}/.rss"
        items = await self._fetch_rss_feed(url)
        for it in items:
            it["source"] = "reddit"
            it["channel_or_feed"] = f"r/{subreddit}"
        return items

    async def sync(self) -> dict[str, Any]:
        """Fetch items from all configured public feeds and store in social_items."""
        all_items: list[dict[str, Any]] = []

        for url in self.rss_urls:
            all_items.extend(await self._fetch_rss_feed(url))

        for handle in self.bluesky_handles:
            all_items.extend(await self._fetch_bluesky_handle(handle))

        for inst in self.mastodon_instances:
            all_items.extend(await self._fetch_mastodon_instance(inst))

        for sub in self.subreddits:
            all_items.extend(await self._fetch_reddit_subreddit(sub))

        now = datetime.now(UTC).isoformat()
        saved_count = 0

        async with get_db() as db:
            for item in all_items:
                await db.execute(
                    """
                    INSERT INTO social_items (
                        id, source, channel_or_feed, author, title,
                        content_sanitized, url, published_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        content_sanitized = excluded.content_sanitized
                    """,
                    (
                        item["id"],
                        item["source"],
                        item["channel_or_feed"],
                        item["author"],
                        item["title"],
                        item["content"],
                        item["url"],
                        item["published_at"],
                        now,
                    ),
                )
                saved_count += 1
            await db.commit()

        await self.mark_sync(status="ok", reason=f"Synced {saved_count} social feed items")
        return {"saved_count": saved_count}

    async def fetch(self, query: str | None = None, limit: int = 50) -> list[Any]:
        sql = (
            "SELECT id, source, channel_or_feed, author, title, content_sanitized, "
            "url, published_at FROM social_items WHERE deleted_at IS NULL"
        )
        params: list[Any] = []
        if query:
            sql += " AND (title LIKE ? OR content_sanitized LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        async with get_db() as db:
            async with db.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "source": r[1],
                        "channel_or_feed": r[2],
                        "author": r[3],
                        "title": r[4],
                        "content": r[5],
                        "url": r[6],
                        "published_at": r[7],
                    }
                    for r in rows
                ]

    async def search(self, query: str, limit: int = 20) -> list[Any]:
        return await self.fetch(query=query, limit=limit)

    async def guarded_write(
        self,
        action: str,
        payload: dict[str, Any],
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Feeds connector is strictly read-only for public feeds.")

    async def generate_social_digest(self, limit: int = 20) -> dict[str, Any]:
        """Aggregate recent social and feed items into a structured digest."""
        items = await self.fetch(limit=limit)
        by_source: dict[str, list[dict[str, Any]]] = {}
        for it in items:
            by_source.setdefault(it["source"], []).append(it)
        return {
            "total_items": len(items),
            "sources": list(by_source.keys()),
            "breakdown": {k: len(v) for k, v in by_source.items()},
            "items_by_source": by_source,
        }
