# Verified Sources Audit (Phase 3 Live Probe)

Audit Date: 2026-10-31
Verification: VERIFIED-LIVE (Live probe using WebResearcher with async DNS, defusedxml, and per-hop SSRF validation)

## Live Sweep Results

| Source Name | Topic | URL | Type | Status | HTTP | Latency | Items Extracted | Verification |
|---|---|---|---|---|---|---|---|---|
| arXiv cs.AI | topic_ai | https://export.arxiv.org/rss/cs.AI | RSS | PASS | 200 | 1908.3ms | 15 | VERIFIED-LIVE |
| Hacker News | topic_cs | https://news.ycombinator.com/rss | RSS | PASS | 200 | 1956.1ms | 15 | VERIFIED-LIVE |
| The Hacker News | topic_cybersecurity | https://feeds.feedburner.com/TheHackersNews | RSS | PASS | 200 | 3132.8ms | 15 | VERIFIED-LIVE |
| CISA Alerts | topic_cybersecurity | https://www.cisa.gov/cybersecurity-advisories/all.xml | RSS | PASS | 200 | 1787.0ms | 15 | VERIFIED-LIVE |
| Hackaday | topic_iot | https://hackaday.com/feed/ | RSS | PASS | 200 | 2308.6ms | 7 | VERIFIED-LIVE |
| IEEE Spectrum | topic_robotics | https://spectrum.ieee.org/feeds/topic/robotics.rss | RSS | PASS | 200 | 2429.6ms | 15 | VERIFIED-LIVE |
| Ars Technica | topic_innovations | https://feeds.arstechnica.com/arstechnica/index | RSS | PASS | 200 | 5153.4ms | 15 | VERIFIED-LIVE |
| TechCrunch | topic_startups | https://techcrunch.com/feed/ | RSS | PASS | 200 | 1938.6ms | 15 | VERIFIED-LIVE |
| The Verge | topic_ai_models | https://www.theverge.com/rss/index.xml | RSS | PASS | 200 | 1700.8ms | 10 | VERIFIED-LIVE |
| CTFtime Upcoming | topic_ctfs | https://ctftime.org/event/list/upcoming/rss/ | RSS | PASS | 200 | 1700.1ms | 15 | VERIFIED-LIVE |
| Unstop Competitions | topic_competitions | https://unstop.com/competitions | Web | PASS | 200 | 4617.4ms | 1 | VERIFIED-LIVE |

## Failed / Excluded Feeds (Historic)
- BleepingComputer (`https://www.bleepingcomputer.com/feed/`): HTTP 403 Forbidden (Cloudflare bot block on server-side scraping).
- Devpost (`https://devpost.com/hackathons`): HTTP 403 Forbidden (Cloudflare WAF).

## Safeguards Applied
1. **Defused XML**: XML parsing switched from standard `xml.etree` to `defusedxml.ElementTree` preventing entity expansion attacks (Billion Laughs) and external DTD resolution.
2. **Async DNS Resolution**: Hostname lookup using non-blocking `asyncio.get_running_loop().getaddrinfo`.
3. **Per-hop SSRF Protection**: Full traversal inspection preventing redirects from public URLs to private (RFC 1918), loopback (`127.0.0.0/8`), cloud metadata (`169.254.169.254`), or link-local addresses.
4. **NAT64 RFC 6052 Compatibility**: Safely unwraps `64:ff9b::/96` IPv4-mapped addresses to evaluate underlying IPv4 security.
