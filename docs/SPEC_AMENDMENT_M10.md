# SPEC_AMENDMENT_M10.md — Fast Front Desk (NVIDIA API) with Automatic Local Fallback, Task Center, Privacy Policy v2

This document contains the binding specification amendment for Milestone 10. It supersedes the conflicting parts of Spec Sections 2, 6.2, 6.3 and 6.4 for chat handling. Where this document conflicts with `docs/SPEC.md`, this document wins. Where it is silent, `docs/SPEC.md` wins.

---

## 1. Spec amendment (binding for this session)

### 1.1 Goals

- **G1. Fast chat.** Every interactive turn is answered by a cloud model through the NVIDIA API, streamed, whenever the cloud is healthy.
- **G2. Seamless fallback.** If the key is missing, invalid, throttled, slow, or the machine is offline, chat is answered by the local model with the same interface and a visible notice. The owner never sees a hard failure caused by the cloud being unavailable.
- **G3. Automatic intent analysis.** Each request is classified (reply now, reminder, schedule, job edit, background task, deep research, memory operation, system action, sensitive, ambiguous) with a decision on `reply_mode`: `instant`, `background`, or `both`.
- **G4. Background work with notifications.** Anything that is not a quick answer is queued as a task, executed in the background on the right lane, and the owner is notified on completion, failure, or when approval is needed.
- **G5. Always-ready small local model.** A small local model stays loaded for quick local work; the large local model loads on demand.
- **G6. Honest privacy.** Cloud chat is allowed by the owner's choice, bounded by data classes, an egress filter, a ledger, and a private mode.

### 1.2 Data classes (replace the single `PRIVATE` flag)

| Class | Examples | Cloud allowed? |
|---|---|---|
| `SECRET` | API keys, tokens, passwords, private keys, ID and card numbers | **Never.** Masked by the egress filter even if found inside chat text. |
| `LOCAL_ONLY` | File contents, memory facts (unless marked `cloud_ok`), future email, calendar, camera, screen content, shell output | **Never.** |
| `CHAT` | The owner's own message text and conversation history | Yes, when `privacy.chat_to_cloud = true` and the conversation is not in Private mode and the message is not flagged sensitive. |
| `PUBLIC` | Web pages, search results, RSS items | Yes. |

The taint model gains these classes (keep `UNTRUSTED` as an independent flag). **Router rule:** a request may go to a non-local provider **only if** it contains no `SECRET` and no `LOCAL_ONLY` content after the egress filter has run. Plain unlabeled strings are treated as `LOCAL_ONLY` (fail closed). This replaces the earlier rule that all chat is private.

### 1.3 Decisions already made by the owner and by this amendment

- Chat text goes to the NVIDIA API by default when the cloud is healthy (`privacy.chat_to_cloud = true`).
- **Memory facts are `local_only` by default.** The cloud-safe "profile card" is **empty by default** (`privacy.cloud_profile_card = false`). The owner can mark individual facts `cloud_ok` in the UI or via a tool. Questions about private facts are routed to the local lane.
- The cloud lane never receives file contents, shell output, or any `LOCAL_ONLY` data. The cloud front desk may call only the **front-desk tool allowlist**: `web_search`, `fetch_page`, and the dispatch tools defined in F4. It never calls file, shell, memory-write, or system tools directly; those run through the Task Center on the local lane, with the normal approval rules.
- The NVIDIA trial terms say free endpoints may log request data and may use content to improve products; there is no zero-retention option. **Document this** in `docs/SECURITY.md` and in the Settings page next to the cloud toggle. The cloud lane is a conscious tradeoff, not a private channel.
- The free-tier budget is treated as about 30 requests per minute (configurable). Interactive turns have a reserved share.

### 1.4 Component overview

```
message ──► Egress/sensitivity check ──► Reflex router (no LLM, <50 ms) ──┐
                                                                         ▼
                                        ┌───────────────  Front Desk  ───────────────┐
                                        │ cloud (NVIDIA) if CloudHealth=ACTIVE       │
                                        │ else local (resident/brain) same interface │
                                        └───────┬─────────────────────────┬──────────┘
                                         streamed reply            dispatch tools
                                                                         ▼
                                                                   Task Center
                                      ┌───────────────┬──────────────────┼───────────────┐
                                 Local worker    Cloud worker       Scheduler bridge   Approval parking
                                 (resident /     (public research,   (no LLM at fire)
                                  brain)          plan/synthesis)
                                                                         ▼
                                                 NotificationService (desktop, UI inbox, toast)
```

### 1.5 Cloud health state machine

States: `ACTIVE`, `NO_KEY`, `INVALID_KEY` (HTTP 401/403), `RATE_LIMITED` (HTTP 429 or budget exhausted, with cooldown), `DEGRADED` (repeated timeouts or TTFT above threshold), `OFFLINE` (DNS or connection failure). A circuit breaker opens after N consecutive failures (default 3) and half-opens after a cooldown (default 60 s) with a single probe. Transitions use hysteresis so the state does not flap. The current state, reason and since-time are exposed in `/api/status`, `nx cloud status`, `nx doctor`, and a UI indicator. **Any state other than `ACTIVE` means the front desk runs locally.**
