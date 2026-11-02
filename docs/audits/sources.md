# Aggregator Probe Audit (Session K)

Conducted on: `2026-11-02`  
Agent: `NoX/0.1.0 (Desktop Intelligence Agent; +https://github.com/callme-ADHI/NoX---AI)`  
Purpose: Probe major hackathon, competition, and challenge aggregators at startup; evaluate robots.txt compliance, HTTP response codes, and Trafilatura readable text extraction.

---

## 1. Live Aggregator Probe Results

| Aggregator | Target URL | HTTP Status | robots.txt Allowed | Raw HTML Size | Trafilatura Extracted Text | Assessment & Strategy |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **Devpost** | `https://devpost.com/hackathons` | `200 OK` | `True` | 152,750 B | 973 chars | **Partial / Listing root**: Contains individual upcoming hackathons in HTML payload, but root `/hackathons` URL is a generic listing. Links to individual subdomains (`https://*.devpost.com`) must be extracted and followed. |
| **Unstop** | `https://unstop.com/hackathons` | `200 OK` | `True` | 24,265 B | 160 chars | **Client SPA**: Heavily dynamic React hydration. Static GET returns minimal text. Must use search discovery or specific program URLs. |
| **MLH** | `https://mlh.io/seasons/2025/events` | `200 OK` | `True` | 216,674 B | 108 chars | **Client SPA**: Event list rendered in browser via client-side components. |
| **Hackathons.ca** | `https://hackathons.ca` | `Timeout` | N/A | 0 B | 0 chars | **Unreachable**: Connection timed out. Dropped from live candidate pool. |
| **Kaggle** | `https://www.kaggle.com/competitions` | `200 OK` | `True` | 5,132 B | 0 chars | **Client SPA Shell**: React app shell without server-side table markup. Trafilatura returns empty text. Direct competition URLs or RSS required. |
| **CTFtime** | `https://ctftime.org/event/list/upcoming` | `200 OK` | `True` | 20,644 B | 3,667 chars | **Fully Parseable**: Semantic HTML table of upcoming CTFs with dates, formats, rating weights, and event links. High extraction reliability. |

---

## 2. Extraction & Ingestion Strategy

1. **Individual Events Only (Spec §4.4)**:
   - Root listing URLs (e.g. `https://devpost.com/hackathons`, `https://mlh.io/seasons`) are strictly disallowed from appearing as briefing items.
   - For hackathons, individual event URLs (e.g., `https://hackmit.org`, `https://calhacks.io`, or `https://<event>.devpost.com`) are parsed and validated.

2. **Search Discovery Integration (Spec §4.2)**:
   - Aggregators that render client-side only (Kaggle, Unstop, MLH) are supplemented via DuckDuckGo search queries targeting specific upcoming events with server-rendered landing pages.

3. **Robots.txt & Rate Limiting (Spec §4.2)**:
   - Every domain's `robots.txt` is fetched, parsed, and respected before extraction. If disallow matches the user agent, the link is logged and dropped.
   - Per-domain rate limit enforced (default 1.0 RPS).
