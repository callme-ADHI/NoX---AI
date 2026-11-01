"""Polite article collector, feed parser, readability extractor, and anti-SSRF validator."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import defusedxml.ElementTree as ET
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

    def _is_ip_safe(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            return False
        # Handle RFC 6052 / NAT64 well-known prefix 64:ff9b::/96
        if ip.version == 6 and ip in ipaddress.IPv6Network("64:ff9b::/96"):
            v4 = ipaddress.IPv4Address(ip.packed[-4:])
            return not (v4.is_private or v4.is_loopback or v4.is_link_local or v4.is_multicast or v4.is_unspecified or v4.is_reserved)
        if ip.is_reserved:
            return False
        return True

    async def is_ssrf_safe(self, url: str) -> bool:
        """Validate URL is public HTTP/HTTPS and does not resolve to private/loopback/cloud metadata IP."""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            hostname = parsed.hostname
            if not hostname:
                return False

            # Check loopback or IP literal directly
            try:
                ip = ipaddress.ip_address(hostname)
                return self._is_ip_safe(ip)
            except ValueError:
                pass

            # Async DNS resolve check
            loop = asyncio.get_running_loop()
            addr_info = await loop.getaddrinfo(hostname, None)
            if not addr_info:
                return False
            for item in addr_info:
                ip_str = item[4][0]
                ip_obj = ipaddress.ip_address(ip_str)
                if not self._is_ip_safe(ip_obj):
                    return False
            return True
        except Exception:
            return False

    async def _safe_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        max_redirects: int = 5,
        **kwargs: Any,
    ) -> httpx.Response:
        """Perform request with per-hop SSRF validation across all redirects."""
        current_url = url
        req_headers = headers or {"User-Agent": self.config.research.user_agent}

        async with httpx.AsyncClient(timeout=timeout, verify=True) as client:
            for hop in range(max_redirects + 1):
                if not await self.is_ssrf_safe(current_url):
                    raise ValueError(f"SSRF unsafe URL blocked at hop {hop}: {current_url}")

                if method.upper() == "HEAD":
                    resp = await client.head(current_url, headers=req_headers, follow_redirects=False, **kwargs)
                elif method.upper() == "GET":
                    resp = await client.get(current_url, headers=req_headers, follow_redirects=False, **kwargs)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                if resp.is_redirect:
                    loc = resp.headers.get("location")
                    if not loc:
                        return resp
                    # Resolve relative redirect
                    current_url = str(resp.url.join(loc))
                else:
                    return resp

        raise httpx.TooManyRedirects(f"Exceeded max redirects ({max_redirects}) for {url}")

    async def fetch_feed(
        self, feed_url: str, source_name: str, topic_id: str
    ) -> list[dict[str, str]]:
        """Fetch RSS/Atom feed and return raw entry dicts."""
        if not await self.is_ssrf_safe(feed_url):
            log.warning("SSRF unsafe feed URL rejected: %s", feed_url)
            return []

        domain = urlparse(feed_url).netloc
        lock = self._get_domain_lock(domain)

        async with lock:
            await asyncio.sleep(1.0 / self.config.research.per_domain_rps)
            try:
                resp = await self._safe_request(
                    "GET",
                    feed_url,
                    timeout=self.config.research.fetch_timeout_s,
                )
                if resp.status_code != 200:
                    log.warning("Feed %s returned HTTP %d", feed_url, resp.status_code)
                    return []
                text = resp.text
            except Exception as exc:
                log.warning("Error fetching feed %s: %s", feed_url, exc)
                return []

        entries: list[dict[str, str]] = []
        try:
            root = ET.fromstring(text)
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
        """Verify link exists and returns HTTP 200 / 30x with SSRF guard."""
        try:
            resp = await self._safe_request("HEAD", url, timeout=5.0)
            if resp.status_code in (200, 301, 302, 307, 308):
                return True
            # Retry with GET for servers blocking HEAD
            resp_get = await self._safe_request(
                "GET",
                url,
                timeout=5.0,
                headers={"User-Agent": self.config.research.user_agent, "Range": "bytes=0-100"},
            )
            return resp_get.status_code in (200, 206, 301, 302)
        except Exception:
            return False

    async def extract_article(self, url: str) -> str:
        """Fetch article HTML with SSRF guard and extract readable text via trafilatura."""
        if not await self.is_ssrf_safe(url):
            return ""
        try:
            resp = await self._safe_request("GET", url, timeout=4.0)
            if resp.status_code != 200:
                return ""
            import trafilatura

            text = trafilatura.extract(
                resp.text,
                include_comments=False,
                include_tables=True,
                output_format="txt",
            )
            return (text or "").strip()
        except Exception as exc:
            log.debug("Trafilatura extraction error on %s: %s", url, exc)
            return ""


