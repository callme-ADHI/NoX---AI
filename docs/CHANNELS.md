# Interactive Channels & Public Syndicated Feeds

This document details NoX's architecture, pairing workflows, and privacy controls for remote communication channels and external syndicated feeds.

---

## 1. Supported Interactive Channels

NoX provides bidirectional and unassisted notification channels designed for secure personal operation on Linux/Hyprland:

### 1.1 Telegram Bot
- **Architecture**: Asynchronous long-polling client (`httpx` to Telegram Bot API).
- **Owner Pairing Protocol**:
  1. The user registers a bot via `@BotFather` and obtains a bot token.
  2. The token is stored locally using `nx channel telegram --token <BOT_TOKEN>` or prompted securely into `SecretStore`.
  3. NoX generates an ephemeral 6-digit cryptographic pairing code (`nx channel telegram --pair`).
  4. The user sends `/pair <code>` to the bot in their private Telegram chat.
  5. NoX binds the user's numeric `chat_id` as the channel `owner_id` in SQLite WAL (`channels` table).
  6. All subsequent commands from non-owner IDs are strictly rejected and logged to the tamper-evident audit log.
- **Commands**:
  - `/status`: Daemon status, model health, system load, active tasks.
  - `/briefing`: Request and stream current daily briefing.
  - `/approve <code>`: Resolve pending approval request remotely.
  - `/panic`: Trigger immediate system killswitch (`nx panic`).
- **Egress Privacy Filter**: Outbound text is scanned and redacted through `PIIRedactor` before transmission. Secrets, tokens, and PII are stripped.

### 1.2 Slack Channel
- **Architecture**: Slack Web API (`chat.postMessage`, `conversations.history`) via bot token.
- **Security & Approvals**: Outbound messages targeting public or shared channels require human approval via `ApprovalsCenter` unless pre-cleared for the verified owner workspace.
- **Ingestion**: Ingests mentions and designated channel messages into `social_items` table with prompt-injection sanitization.

### 1.3 Discord Bot
- **Architecture**: Discord REST API v10 client.
- **Security & Approvals**: Direct message and server posting require confirmation. Inbound payloads are sanitized before agent ingestion.

---

## 2. Public Syndicated Feeds

Rather than intrusive scrapers, NoX ingests external intelligence through structured, compliant syndication:
- **RSS / Atom**: Full standard feed parsing via `feedparser` with timeout and payload truncation controls.
- **Bluesky**: Unauthenticated public ATProto API endpoints (`app.bsky.feed.getAuthorFeed`, search).
- **Mastodon**: Public REST timelines (`/api/v1/timelines/tag/...`, `/api/v1/timelines/public`).
- **Reddit**: Standard public `.rss` feeds (no authenticated scraping).

All syndicated items are deduplicated by `external_id` / URL and stored in `social_items` with full-text search and daily digest summarization (`nx feeds digest`).

---

## 3. Explicit Architectural Exclusions & Rationale

NoX deliberately rejects certain channels and ingestion techniques:

### 3.1 WhatsApp (Meta)
- **Exclusion**: No reverse-engineered WhatsApp Web (Baileys, Puppeteer, selenium) or unofficial multi-device clients.
- **Rationale**:
  1. **Terms of Service Violation**: Meta actively bans phone numbers connected to unauthorized automation.
  2. **Security & Session Risk**: Storing session tokens and multi-device keys locally exposes WhatsApp account control if host compromise occurs.
  3. **Brittle Protocol**: WhatsApp Web internal websocket protocols break frequently without notice, creating high maintenance overhead and false negatives.
  4. **Commercial API Barrier**: The official WhatsApp Cloud API requires Facebook Business Verification, webhook infrastructure, and per-conversation billing unsuited for an offline-first Linux workstation daemon.

### 3.2 Web & Social Scraping (X/Twitter, LinkedIn, Instagram)
- **Exclusion**: No DOM scrapers, headless browser harvesting, or cookie-hijacking for restricted social networks.
- **Rationale**:
  1. **Anti-Bot Countermeasures**: Modern platforms enforce aggressive Cloudflare challenges, fingerprinting, and behavioral CAPTCHAs that fail silently or lead to account bans.
  2. **Attack Surface**: Untrusted web pages containing prompt-injection payloads (invisible text, zero-font instructions) ingested into the agent context could compromise the local environment.
  3. **Stability**: Public syndicated feeds (RSS, Bluesky, ActivityPub) provide clean, structured data with predictable schemas and zero anti-bot friction.

---

## 4. Privacy & Taint Invariant

1. **Local-First Boundary**: Social and channel items ingested from external networks are tagged with provenance metadata (`source`, `external_id`, `author`).
2. **Untrusted Input Scanning**: Inbound messages from external users pass through `InputScanner` to detect prompt injection attempts before being passed to reasoning models.
3. **Guarded Egress**: Outgoing posts or messages triggered by automated skills require explicit human approval via the Approvals Center.
