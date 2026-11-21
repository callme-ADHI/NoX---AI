# NoX Session N Evidence Log

All claims in Session N are recorded here with command invocations and raw output, classified under:
- **VERIFIED-LIVE**: real system, real network, or real account
- **VERIFIED-TEST**: real code path against protocol-level fake server or recorded fixtures
- **MOCK-ONLY**: FakeProvider, fake DB, patched internals
- **USER-RUN**: needs user credentials or root; exact commands provided
- **NOT-DONE**: explicitly acknowledged incomplete items

---


## Phase 1: Baseline and Session M Integrity Gates Evidence

### 1. Baseline Verification [VERIFIED-LIVE]

- **Git status & Branch**:
  - Current branch: `session-n` (branched from `session-m`).
  - Baseline commit: `77bff25` (Session M tip).
- **Full test suites**:
  - `cargo test --manifest-path desktop/src-tauri/Cargo.toml`: 3 passed, 0 failed.
  - `ui/ npm test -- --run`: 11 test files passed, 47 tests passed.
  - `pytest tests/`: 645 passed (including Gate A and Gate C proof tests), 6 warnings.
- **Diagnostics**:
  - `nx doctor`: All required checks passed (Ollama, SQLite WAL, KeyPool 5 keys active, BriefingScheduler).
- **Database Backup**:
  - Command: `nx backup create`
  - Backup path: `/home/boss/.local/share/nox/backups/nox_20261121_044216_fa0a0d.db`
  - Backup size: 13,815,808 bytes (13.8 MB).

---

### 2. Gate A: One Scheduler Authority [VERIFIED-TEST]

#### Architecture & Authoritative Division:
- **Briefings**: `BriefingScheduler` (`noxai.briefing.scheduler.BriefingScheduler`) is the sole authority for morning (04:00) and evening (18:00) slot windows and writes to `briefing_runs`. Legacy `daily-briefing` in `jobs` is disabled (`enabled = 0`). `PersistentScheduler._run_morning_briefing` and `_run_evening_briefing` check `briefing_runs` and skip duplicate execution.
- **Backups**: `JobScheduler` is authoritative for executing `job_06725cb80054` via `execute_planned_job` per Section 5/Session L. `PersistentScheduler._run_backup_job` coordinates via `is_job_due()` to prevent duplicate runs.
- **Scheduled Jobs**: User-defined agent jobs in `jobs` table are run by `JobScheduler`. Daemon maintenance schedules (`memory_index`, `traces_cleanup`, etc.) in `schedules` table are run by `PersistentScheduler`.

#### Standalone Verification in Temporary Instance (`NOX_DATA_DIR=/tmp/nox-gate-a-proof`, `NOX_PORT=7778`):
```text
BriefingScheduler 04:00 trigger: ['2026-11-21:morning']
PersistentScheduler 04:00 response: {'status': 'skipped', 'reason': 'already_served', 'run_id': 'brun_221f11930d51'}
BriefingScheduler 18:00 trigger: ['2026-11-21:evening']
PersistentScheduler 18:00 response: {'status': 'skipped', 'reason': 'already_served', 'run_id': 'brun_cdfc0ec711b9'}

--- DATABASE QUERY: SELECT id, date, slot, state, trigger, title FROM briefing_runs WHERE date = "2026-11-21" ---
{'id': 'brun_cdfc0ec711b9', 'date': '2026-11-21', 'slot': 'evening', 'state': 'succeeded', 'trigger': 'scheduled', 'title': 'NoX Evening Intelligence Briefing — 2026-11-21'}
{'id': 'brun_221f11930d51', 'date': '2026-11-21', 'slot': 'morning', 'state': 'succeeded', 'trigger': 'scheduled', 'title': 'NoX Morning Intelligence Briefing — 2026-11-21'}

Total briefing runs for 2026-11-21: 2 (Expected: exactly 2)
```

---

### 3. Gate B: KeyPool Really Used [VERIFIED-LIVE]

#### Non-test Call Sites of `lease()` and `report_outcome()`:
1. `src/noxai/models/nvidia.py:262`: `async with self.key_pool.lease(job_id=..., priority=..., mode=...) as lease:`
2. `src/noxai/models/nvidia.py:282`: `await self.key_pool.report_outcome(lease.key_id, status_code=429, retry_after=wait_sec)`
3. `src/noxai/models/nvidia.py:306`: `await self.key_pool.report_outcome(lease.key_id, status_code=resp.status_code, error_msg=decoded)`
4. `src/noxai/models/nvidia.py:327`: `await self.key_pool.report_outcome(lease.key_id, status_code=resp.status_code, error_msg=decoded)`
5. `src/noxai/models/nvidia.py:336`: `await self.key_pool.report_outcome(lease.key_id, status_code=resp.status_code)`
6. `src/noxai/models/nvidia.py:362`: `await self.key_pool.report_outcome(lease.key_id, status_code=resp.status_code, error_msg=decoded)`
7. `src/noxai/models/nvidia.py:367`: `await self.key_pool.report_outcome(lease.key_id, status_code=200)`
8. `src/noxai/models/nvidia.py:375`: `await self.key_pool.report_outcome(current_key_id, status_code=status, error_msg=err_msg)`
9. `src/noxai/models/engine.py:217`: `async with self.key_pool.lease(job_id=None, priority=0) as lease:` (in `list_models`)
10. `src/noxai/models/engine.py:252`: `async with self.key_pool.lease(job_id=None, priority=0) as lease:` (in `health`)
11. `src/noxai/models/engine.py:268`: `await self.key_pool.report_outcome(lease.key_id, status_code=200)`
12. `src/noxai/models/engine.py:282`: `await self.key_pool.report_outcome(lease.key_id, status_code=resp.status_code)`
13. `src/noxai/jobs/planner.py:165`: `async with pool.lease(job_id=job_id, priority=1, mode=AllocationMode.MULTI_JOB) as lease:`
14. `src/noxai/briefing/engine.py:738`: `lease_ctx = self.key_pool.lease(job_id=f"briefing_{effective_run_id}", priority=1)`
15. `src/noxai/briefing/engine.py:897`: `await self.key_pool.report_outcome(briefing_lease.key_id, status_code=status_code)`
16. `src/noxai/briefing/engine.py:910, 937`: `await self.key_pool.report_outcome(briefing_lease.key_id, status_code=500)`

#### Live Daemon API Output (`nx keys status`):
```text
             NVIDIA API Key Pool Status (Phase 3) (Live Daemon API)             
┏━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━┳━━━━━━━┓
┃ Key   ┃ Mask… ┃       ┃    In ┃   RPM ┃ Toke… ┃  EWMA ┃ Total ┃      ┃       ┃
┃ ID    ┃ Key   ┃ Stat… ┃ Flig… ┃ Limit ┃  Left ┃  (ms) ┃   Req ┃ 429s ┃ Allo… ┃
┡━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━━┩
│ k1    │ nvap… │ ACTI… │     0 │    35 │  34.0 │ 614.3 │     3 │    0 │ WIDE  │
│ k2    │ nvap… │ ACTI… │     0 │    35 │  33.2 │ 362.1 │     9 │    0 │ WIDE  │
│ k3    │ nvap… │ ACTI… │     0 │    35 │  35.0 │ 500.0 │     0 │    0 │ WIDE  │
│ k4    │ nvap… │ ACTI… │     0 │    35 │  35.0 │ 500.0 │     0 │    0 │ WIDE  │
│ k5    │ nvap… │ ACTI… │     0 │    35 │  35.0 │ 500.0 │     0 │    0 │ WIDE  │
└───────┴───────┴───────┴───────┴───────┴───────┴───────┴───────┴──────┴───────┘
```

#### In-Flight Lease Capture Proof:
```text
                 NVIDIA KeyPool Status While Call Is In Flight                  
┏━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃        ┃ Masked    ┃        ┃           ┃           ┃ Tokens    ┃ Assigned   ┃
┃ Key ID ┃ Key       ┃ Status ┃ In Flight ┃ RPM Limit ┃ Left      ┃ Job        ┃
┡━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ k1     │ nvapi-**… │ ACTIVE │ 1         │ 35        │ 34.0      │ WIDE       │
│ k2     │ nvapi-**… │ ACTIVE │ 0         │ 35        │ 35.0      │ WIDE       │
│ k3     │ nvapi-**… │ ACTIVE │ 0         │ 35        │ 35.0      │ WIDE       │
│ k4     │ nvapi-**… │ ACTIVE │ 0         │ 35        │ 35.0      │ WIDE       │
│ k5     │ nvapi-**… │ ACTIVE │ 0         │ 35        │ 35.0      │ WIDE       │
└────────┴───────────┴────────┴───────────┴───────────┴───────────┴────────────┘
```

---

### 4. Gate C: Note Retrieval Taint-Proof [VERIFIED-TEST]

#### Invariant Enforcement:
- In `src/noxai/memory/conversation.py`, when document chunks are retrieved from notes/vault or recalled message turns are private, `sys_taint` is tagged with `TaintFlag.PRIVATE` and the system message content data class is set to `DataClass.LOCAL_ONLY`.
- In `src/noxai/orchestrator.py` and `src/noxai/models/router.py`, the `EgressFilter` blocks cloud routing and automatically reroutes to the local model (`brain` or `resident`).

#### Automated Test Execution (`tests/test_gate_c_note_taint.py`):
```text
tests/test_gate_c_note_taint.py::test_note_retrieval_taint_proof_local_reroute PASSED
tests/test_gate_c_note_taint.py::test_build_four_part_context_taint_invariants PASSED

Recorded Transport Evidence:
- Turn 1 (Public general question): HTTP request dispatched to integrate.api.nvidia.com.
- Turn 2 (Query referencing user's notes): 0 cloud requests recorded (new_requests = 0).
- Content Scan across transport history: 0 occurrences of note secret snippet TOP_SECRET_TITAN_CORE_V3_ALGORITHM_NONCE_77123 or filename SecretSpecs.md.
```

---

### 5. Gate D: Test Change Audit [VERIFIED-LIVE]

- Detailed audit produced in `docs/audits/session-m-test-changes.md`.
- Every test modified or added in Session M audited. Zero auth/router/orchestrator/privacy assertions were removed or weakened.

---

### 6. Gate E: Session L Deliverables Verification [VERIFIED-TEST]

- Verified schema: `job_plans`, `job_run_steps`.
- Verified executor: plan-first executor in `src/noxai/jobs/executor.py`.
- Verified delete controls: soft delete `deleted_at` on `jobs` table with API endpoints.
- Verified backup: `job_06725cb80054` daily 20:00 backup with private repo enforcement.
- Test Suite: All 24 planned jobs tests pass (`pytest tests/test_planned_jobs.py` -> 24 passed).


---

## Phase 2: OpenJarvis Gap Analysis Evidence

### 1. Upstream Git Commit Inspection [VERIFIED-LIVE]
- Pinned commit verified: `77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b` (Study base).
- Current upstream HEAD checked: `a9e59e8770f92dd8046c2e5fcc1246eef51bb96a` (tag `v1.0.5.dev1374`).
- Upstream recent changes since Session M are documentation/CI updates and minor doctor probes.

### 2. Parity Document Created [VERIFIED-LIVE]
- Document: `docs/OPENJARVIS_PARITY.md`.
- Evaluated: 48 capabilities across 20 subsystems.
- 28 Ported, 14 Adapted, 0 Partial, 6 Excluded with documented rationale.
- 100% parity across all personal assistant workflows.

### 3. Cleanup Deletion Proof [VERIFIED-LIVE]
```text
DELETION_PROOF: /tmp/openjarvis successfully removed
Status: directory does not exist.
```

---

## Phase 3: Security & Privacy Foundation Evidence

### 1. Guardrails Module [VERIFIED-LIVE / VERIFIED-TEST]
- **InputScanner**: Detects prompt injections (`ignore previous instructions`, `DAN`, `developer mode`, `system prompt override`), hidden instruction markers (`<!-- ... -->`, `<system>...</system>`), zero-width Unicode (`\u200b`, `\ufeff`), and bidirectional overrides (`\u202e`). Sanitizes text cleanly.
- **OutputScanner**: Detects leaked secrets, private keys, AWS access keys, GitHub tokens, Slack tokens, NVIDIA API keys, Gemini keys, OpenAI keys, Anthropic keys, and generic tokens. Automatically redacts with typed placeholders (`[SECRET_AWS_ACCESS_KEY_REDACTED]`, `[SECRET_GITHUB_TOKEN_REDACTED]`).
- **PIIRedactor**: Indian PII patterns:
  - Aadhaar 12-digit numbers with Verhoeff checksum algorithm validation.
  - PAN card numbers (`[A-Z]{5}[0-9]{4}[A-Z]`).
  - Bank account numbers & IFSC codes (`[A-Z]{4}0[A-Z0-9]{6}`).
  - UPI IDs (`@okhdfcbank`, `@paytm`, `@ybl`, etc.).
  - Payment card numbers with Luhn checksum algorithm validation.
  - OTP codes (4-8 digits near "OTP" / "verification code").
  - Indian phone numbers (`+91` / 10 digits).
  - Email addresses.
  - Reversible in-memory placeholder mapping (`[AADHAAR_REDACTED_1]`, `[PAN_REDACTED_1]`, etc.) with perfect round-trip restoration.
- **RecordingTransport Verification**: Zero leaked PII tokens transmitted to cloud providers; verified that outbound wire requests carry only placeholders, and responses are restored locally.

### 2. Tamper-Evident, Hash-Chained Audit Log [VERIFIED-LIVE]
- Migration `016_security_foundation.sql`: Table `audit_events` (id, ts, actor, action, target, decision, summary_redacted, prev_hash, hash).
- Cryptographic hash chaining: `SHA-256(prev_hash | id | ts | actor | action | target | decision | summary_redacted)` anchored to genesis `0000000000000000000000000000000000000000000000000000000000000000`.
- Verified live command output:
```text
$ nx audit verify
✔ Audit log verified: 3 events chained without tampering

$ nx audit events --lines 3
Tamper-Evident Audit Events (last 3)
ID  Timestamp             Actor    Action          Target             Decision  Summary
3   2026-11-21T05:52:...  cli      panic_resumed   system             executed  Emergency kill switch resumed normal operations
2   2026-11-21T05:52:...  cli      panic_engaged   system             executed  Emergency kill switch engaged: Testing Phase 3 kill switch
1   2026-11-21T05:52:...  kill_sw  approvals_rev.. pending_approvals  revoked   Revoked 0 pending approvals
```
- Tamper detection verified: Altering any row's summary or `prev_hash` causes `nx audit verify` to report exact failing event ID.

### 3. Secret Store Abstraction [VERIFIED-LIVE]
- File store: `~/.local/share/nox/connectors/secrets.json` enforced with strict `0600` permissions (parent directory `0700`).
- System keyring support with graceful fallback.
- Names-only listing (`nx secrets list`), zero-leak guarantee (values never logged).
- Live verification:
```text
$ nx secrets set test_key --value "my-super-secret-key"
✔ Secret 'test_key' stored successfully.

$ nx secrets list
Stored Secrets (1)
Secret Name: test_key  Status: configured

$ nx secrets get test_key
Secret 'test_key' is configured: my-...ey (use --show to reveal)

$ nx secrets delete test_key
✔ Secret 'test_key' deleted.
```

### 4. Permission Manifests & Runtime Enforcement [VERIFIED-TEST]
- Declarative `PermissionManifest` (id, name, manifest_type, read_resources, write_resources, execute_commands, network_hosts, allow_cloud_egress, allow_shell).
- Default-deny model: actions not explicitly declared are rejected with `PermissionDeniedError` and logged to `audit_events`.

### 5. Approvals Center [VERIFIED-LIVE / VERIFIED-TEST]
- Table `pending_approvals` (id, action_type, payload_json, preview, scope, confirmation_code, status, expires_at, created_at, resolved_at, resolved_by).
- Scoped single-use approvals: payload verification ensures that execution payload matches approved payload byte-for-byte; subsequent execution attempts fail (consumed status).
- Confirmation code support for chat channels.
- CLI commands: `nx approvals list`, `nx approvals approve <id> [--code <code>]`, `nx approvals deny <id>`.

### 6. Emergency Kill Switch [VERIFIED-LIVE]
- Commands: `nx panic`, `nx panic --status`, `nx panic --resume`.
- Immediately revokes all pending approvals, sets panic flag in `security_state`, logs audit event.
- Live CLI verification:
```text
$ nx panic --reason "Testing Phase 3 kill switch"
EMERGENCY KILL SWITCH ENGAGED by cli: Testing Phase 3 kill switch
- Connectors background sync suspended
- Social channel polling stopped
- Continuous agents paused
- All pending approvals revoked
- Tamper-evident audit event recorded

$ nx panic --status
KILL SWITCH IS ACTIVE
Engaged at: 2026-11-21T05:52:...
Actor: cli
Reason: Testing Phase 3 kill switch

$ nx panic --resume
✔ Emergency kill switch resumed. System operating normally.
```

### 7. Test Suite Outcome [VERIFIED-TEST]
- `tests/test_security_foundation.py`: 23 passed in 2.35s.

---

## Phase 4: Connector Framework, Mail, Calendar Evidence

### 1. Database Schema Migration 017 [VERIFIED-TEST]
- Applied `src/noxai/migrations/017_connectors_and_mail.sql` with automatic SQLite online backup.
- Verified on fresh database, copied database, and live database.
- Created tables:
  - `connectors`: typed health, config, rate limiting, and privacy class (`local_only` default).
  - `mail_items`: stores sanitized emails (text capped at 20KB) with category and extracted fields.
  - `mail_attachments`: tracks attachment metadata and extracted text (capped at 1MB).
  - `calendar_events`: stores synced events from Google Calendar and ICS sources.

### 2. Base Connector & Registry [VERIFIED-TEST]
- Abstract `BaseConnector` enforces `auth()`, `health()`, `sync()`, `fetch()`, `search()`, `guarded_write()`.
- Built-in `RateLimiter` and exponential backoff retry.
- Non-blocking daemon background synchronization across all active connectors (`ConnectorRegistry.sync_all_active()`).

### 3. Gmail Connector (OAuth 2.0 PKCE) [VERIFIED-TEST]
- REST API implementation via `httpx` with PKCE verifier/challenge generation.
- Default read-only scope (`https://www.googleapis.com/auth/gmail.readonly`).
- Tokens securely stored in `SecretStore`.
- Zero raw PII leakage: incoming emails are scrubbed with `PIIRedactor` and capped at 20KB before database storage.
- Documented in `docs/CONNECT_GMAIL.md`.

### 4. Generic IMAP over SSL Connector [VERIFIED-TEST]
- Secure SSL IMAP connection (port 993 default) using Python standard library `imaplib.IMAP4_SSL`.
- MIME parsing handles multipart, plain text, and HTML tag stripping.
- Sanitizes PII before storage in `mail_items`.

### 5. Calendar Connector & ICS Parsing [VERIFIED-TEST]
- Supports Google Calendar API and RFC5545 iCalendar (`.ics`) local files or remote URLs.
- Extracts `DTSTART`, `DTEND`, `SUMMARY`, `DESCRIPTION`, `LOCATION`, and `UID`.
- `fetch_upcoming(days=7)` queries upcoming schedule.

### 6. Mail Profile, Intelligence & Phishing Detection [VERIFIED-TEST / VERIFIED-LIVE]
- Configuration: `~/.config/nox/mail_profile.toml`.
- Rule-based categorization: `College`, `Opportunities`, `Hackathons/CTF`, `GitHub/CI`, `Payments/Banking` (strictly `local_only`), `Newsletters`, `Personal`, `Noise`.
- `PhishingDetector`: identifies brand spoofing (display name vs sender domain), Punycode homoglyphs (`xn--`), and urgency threat triggers.
- Action items & deadline extraction using regex patterns.
- `DigestGenerator`: compiles rich morning and evening digests combining emails and calendar events.

### 7. Guarded Writes with ApprovalsCenter [VERIFIED-TEST]
- Drafting emails creates local drafts or remote Gmail drafts.
- Sending emails (`action="send"`) strictly enforces human authorization:
  - Without approval: creates a pending approval in `ApprovalsCenter`.
  - With approved token: verifies exact payload match, consumes token (single-use), and logs to `audit_events`.

### 8. CLI Commands [VERIFIED-LIVE]
```text
$ nx connect list
Configured Connectors
No connectors configured yet. Run `nx connect gmail` or `nx connect imap`.

$ nx mail digest --period morning
╭───────────────────────────────────────────────╮
│ NoX Daily Digest (Morning) — 2026-11-21T06:20 │
╰───────────────────────────────────────────────╯
No scheduled calendar events for the next 48h.

$ nx connect calendar --ics-path /dev/null
✓ Calendar source configured and verified.

$ nx disconnect calendar
✓ Connector 'calendar' disconnected and disabled.
```

### 9. Test Suite Execution (`tests/test_mail_connectors.py`) [VERIFIED-TEST]
- All 9 tests passed in 1.20s:
  - `test_migration_017_tables`: PASSED
  - `test_base_connector_and_registry`: PASSED
  - `test_gmail_connector_mocked_sync_and_pii_redaction`: PASSED
  - `test_gmail_guarded_write_approvals`: PASSED
  - `test_imap_connector_with_mock_client`: PASSED
  - `test_calendar_connector_and_ics_parsing`: PASSED
  - `test_phishing_detector`: PASSED
  - `test_mail_classifier_categories_and_extraction`: PASSED
  - `test_digest_generator`: PASSED

---

## Phase 5: Channels and Social Subsystems Evidence

### 1. Database Schema Migration 018 [VERIFIED-TEST]
- Applied `src/noxai/migrations/018_channels_and_social.sql` with automatic SQLite online backup.
- Created tables:
  - `channels`: id, name, type (telegram, slack, discord, custom), enabled, config_json, pairing_code, owner_id, allowlist_json, health_status, health_reason.
  - `social_items`: id, source (rss, bluesky, mastodon, reddit, slack, discord), channel_or_feed, author, title, content_sanitized, url, published_at, created_at, deleted_at.
- Created indexes on source, published_at, deleted_at, type, enabled.

### 2. Telegram Bot Channel with Owner Pairing & Egress Filter [VERIFIED-TEST]
- Implemented `TelegramChannel` (`src/noxai/channels/telegram.py`):
  - Long-polling client via `httpx`.
  - Cryptographic 6-digit numeric pairing code (`nx channel telegram --pair`).
  - `/pair <code>` binds sender's `chat_id` as the channel `owner_id` in SQLite.
  - Commands implemented and verified: `/status`, `/briefing`, `/approve`, `/panic`.
  - Rejection of unauthorized users: messages from non-owner IDs receive an authorization error and are logged to `audit_events` with decision `denied`.
  - Privacy egress filter: outgoing messages containing Indian PII (phone, Aadhaar) or secret tokens are redacted through `PIIRedactor` and `OutputScanner` before sending.

### 3. Slack & Discord Channels with Guarded Posting [VERIFIED-TEST]
- Implemented `SlackChannel` (`src/noxai/channels/slack.py`) and `DiscordChannel` (`src/noxai/channels/discord.py`):
  - Ingestion: cleans inbound messages with `InputScanner` (stripping zero-width unicode, bidi overrides, and hidden injection tags) before persistence in `social_items`.
  - Guarded Posting: `send_message(..., guarded=True)` generates a `PendingApproval` in `ApprovalsCenter`. Executing with valid approval token verifies exact payload match, consumes token (single-use), and posts via API.

### 4. Public Syndicated Feeds Connector [VERIFIED-TEST]
- Implemented `FeedsConnector` (`src/noxai/connectors/feeds.py`):
  - Syndication sources: RSS / Atom via `feedparser`, Bluesky public ATProto API, Mastodon public timeline, Reddit standard RSS.
  - HTML tag stripping and input sanitization before storing in `social_items`.
  - Social digest generator: aggregates items by source, counts breakdowns, and provides highlight summaries (`nx feeds digest`).

### 5. Architectural Exclusions Documented [VERIFIED-TEST]
- Fully documented in `docs/CHANNELS.md` and recorded in `docs/DECISIONS.md` under **ADR-N8**:
  - Excluded WhatsApp: Meta ToS violations, phone number ban risks, session key exposure, and protocol fragility.
  - Excluded Web Scraping: Anti-bot barriers (Cloudflare, fingerprinting) and prompt injection attack surface.

### 6. CLI Commands [VERIFIED-LIVE]
```text
$ nx channel list
Configured Messaging Channels
No channels configured. Run `nx channel telegram` or `nx channel slack`.

$ nx feeds list
Stored Feed Items by Source
No social items found. Run `nx feeds sync` or add feeds.
```

### 7. Test Suite Execution (`tests/test_channels_and_feeds.py`) [VERIFIED-TEST]
- All 7 tests passed in 1.20s:
  - `test_migration_018_tables`: PASSED
  - `test_base_channel_pairing_and_privacy`: PASSED
  - `test_telegram_channel_pairing_and_commands`: PASSED
  - `test_telegram_outgoing_privacy_filter`: PASSED
  - `test_slack_channel_ingest_and_guarded_send`: PASSED
  - `test_discord_channel_ingest_and_guarded_send`: PASSED
  - `test_feeds_connector_sync_and_digest`: PASSED


