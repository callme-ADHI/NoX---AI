"""Polite article collector, feed parser, readability extractor, and anti-SSRF validator."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from noxai.config import Settings

log = logging.getLogger(__name__)


@dataclass
class CollectedItem:
    item_id: str
    url: str
    canonical_url: str
    title: str
    published_at: str | None
    fetched_at: str
    text: str
    source_name: str
    quality_score: float = 1.0


class WebResearcher:
    """Polite, cached, SSRF-guarded article scraper and RSS reader."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self._cache: dict[str, tuple[float, str]] = {}  # url_hash -> (expiry, content)
        self._domain_locks: dict[str, asyncio.Lock] = {}

    def _get_domain_lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._domain_locks:
            self._domain_locks[domain] = asyncio.Lock()
        return self._domain_locks[domain]

    def is_ssrf_safe(self, url: str) -> bool:
        """Validate URL is public HTTP/HTTPS and does not resolve to private IP."""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            hostname = parsed.hostname
            if not hostname:
                return False

            # Check loopback or IP literal
            try:
                ip = ipaddress.ip_address(hostname)
                return not (ip.is_private or ip.is_loopback or ip.is_reserved)
            except ValueError:
                pass

            # DNS resolve check
            addr_info = socket.getaddrinfo(hostname, None)
            for item in addr_info:
                ip_str = item[4][0]
                ip_obj = ipaddress.ip_address(ip_str)
                if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved:
                    return False
            return True
        except Exception:
            return False

    async def fetch_feed(
        self, feed_url: str, source_name: str, topic_id: str
    ) -> list[dict[str, str]]:
        """Fetch RSS/Atom feed and return raw entry dicts."""
        if not self.is_ssrf_safe(feed_url):
            log.warning("SSRF unsafe feed URL rejected: %s", feed_url)
            return []

        domain = urlparse(feed_url).netloc
        lock = self._get_domain_lock(domain)

        async with lock:
            await asyncio.sleep(1.0 / self.config.research.per_domain_rps)
            try:
                async with httpx.AsyncClient(
                    headers={"User-Agent": self.config.research.user_agent},
                    timeout=self.config.research.fetch_timeout_s,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(feed_url)
                    if resp.status_code != 200:
                        log.warning("Feed %s returned HTTP %d", feed_url, resp.status_code)
                        return []
                    text = resp.text
            except Exception as exc:
                log.warning("Error fetching feed %s: %s", feed_url, exc)
                return []

        entries: list[dict[str, str]] = []
        try:
            root = ET.fromstring(text)  # noqa: S314
            # Standard RSS channel -> item
            for item in root.findall(".//item")[:15]:
                t = item.findtext("title") or ""
                link = item.findtext("link") or ""
                pub = item.findtext("pubDate") or ""
                desc = item.findtext("description") or ""
                if link and t:
                    entries.append(
                        {
                            "title": t.strip(),
                            "url": link.strip(),
                            "published_at": pub.strip(),
                            "summary": re.sub(r"<[^>]+>", "", desc).strip()[:300],
                            "source_name": source_name,
                            "topic_id": topic_id,
                        }
                    )

            # Atom feed -> entry
            if not entries:
                for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:15]:
                    t = entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
                    link_elem = entry.find("{http://www.w3.org/2005/Atom}link")
                    link = link_elem.get("href", "") if link_elem is not None else ""
                    pub = (
                        entry.findtext("{http://www.w3.org/2005/Atom}published")
                        or entry.findtext("{http://www.w3.org/2005/Atom}updated")
                        or ""
                    )
                    summary = entry.findtext("{http://www.w3.org/2005/Atom}summary") or ""
                    if link and t:
                        entries.append(
                            {
                                "title": t.strip(),
                                "url": link.strip(),
                                "published_at": pub.strip(),
                                "summary": re.sub(r"<[^>]+>", "", summary).strip()[:300],
                                "source_name": source_name,
                                "topic_id": topic_id,
                            }
                        )
        except Exception as exc:
            log.debug("XML parse error on feed %s: %s", feed_url, exc)

        return entries

    async def verify_link(self, url: str) -> bool:
        """Verify link exists and returns HTTP 200 / 30x."""
        if not self.is_ssrf_safe(url):
            return False
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": self.config.research.user_agent},
                timeout=5.0,
                follow_redirects=True,
            ) as client:
                resp = await client.head(url)
                if resp.status_code in (200, 301, 302, 307, 308):
                    return True
                # Retry with GET for servers blocking HEAD
                resp_get = await client.get(url, headers={"Range": "bytes=0-100"})
                return resp_get.status_code in (200, 206, 301, 302)
        except Exception:
            return False
