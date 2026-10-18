# DECISIONS.md — Architecture and Design Decisions for NoX

This document records every significant design choice made during the NoX
build, including alternatives considered and the reason for the selection.
Updated after each milestone.

---

## M0 — Skeleton

### D-001: Python version — 3.14 on system, using Python 3.14

**Decision:** Use the system Python 3.14.7 directly with a `uv`-managed venv.

**Context:** The spec says "Python 3.11+; use uv and Python 3.12 if dependencies
lag the system Python." The system has Python 3.14.7, which is newer than 3.12.

**Rationale:** All listed dependencies (FastAPI, pydantic v2, httpx, etc.)
support Python 3.11+ and should work on 3.14. If a specific dependency fails
to build on 3.14, we will add a note here and pin a Python 3.12 venv.
No dependency issues were encountered during M0.

---

### D-002: `uv` as package manager

**Decision:** Use `uv` for venv creation and dependency installation.

**Context:** The spec requires `uv`. It was not installed on the system; we
installed it via the official install script to `~/.local/bin/uv`.

**Rationale:** `uv` is faster than pip, produces reproducible installs, and
is the spec's explicit requirement.

---

### D-003: `tomllib` from stdlib

**Decision:** Use stdlib `tomllib` (Python 3.11+) for TOML parsing. No
third-party `tomli` or `toml` package.

**Rationale:** The spec says to use `tomllib`. It is in stdlib since 3.11.
No additional dependency needed.

---

### D-004: Pydantic v2 with `extra="forbid"`

**Decision:** All config models use `model_config = {"extra": "forbid"}`.

**Rationale:** The spec explicitly requires "reject unknown keys with a clear
error." `extra="forbid"` in pydantic v2 does exactly this, and the error
message produced by `ValidationError` is augmented into human-readable output
listing the offending field paths.

---

### D-005: Secrets loaded manually, not via dotenv library

**Decision:** Parse `secrets.env` with 5 lines of regex instead of adding a
`python-dotenv` dependency.

**Rationale:** The format is KEY=VALUE with `#` comments. A full dotenv
library would add a dependency for a feature we can implement trivially and
with complete control over the permission check. The spec says "no global
mutable state" — a library that patches `os.environ` would violate that.

---

### D-006: Vector backend detection at startup

**Decision:** Try loading `sqlite-vec` into a probe in-memory connection at
startup. On failure, set `VECTOR_BACKEND = "numpy"` and log a warning.

**Rationale:** The spec says: "Detect at startup and log which path is active."
The numpy fallback is sufficient for the expected scale (< 50k vectors).
`sqlite-vec` can be added later without a schema change.

---

### D-007: Single migration file for M0

**Decision:** All 20+ tables are created in `001_initial.sql`. Future
milestones add `002_*.sql`, etc.

**Rationale:** The spec says "numbered .sql files." A single initial migration
is the simplest correct approach. The migration runner is idempotent by design.

---

### D-008: FTS5 tables use `content=` for linked tables

**Decision:** `fts_facts` and `fts_episodes` use FTS5 `content=` mode,
meaning FTS5 does not store the document text itself — it is read from the
linked table on demand.

**Rationale:** Avoids storing the same text twice. The trade-off is that
deleted rows require explicit `fts_facts DELETE` triggers (to be added in M4
when full-text search is used). For M0 this is forward-compatible.

---

### D-009: `nx open` is a stub

**Decision:** `nx open` prints "implemented in M7" and exits 1.

**Rationale:** The spec says M0 is only the skeleton. The auth session token
and the Chromium launcher are M7 work. A clean error with the milestone
reference is more honest than a partial implementation.

---

### D-010: Binding to `127.0.0.1` enforced in Pydantic validator

**Decision:** `ServerConfig.host` has a `@field_validator` that rejects any
value other than `127.0.0.1` with a clear message.

**Rationale:** The spec says "never 0.0.0.0 in Phase 1" and lists it as a
security requirement. Enforcing it in the config validator means the server
cannot even start if misconfigured — belt and suspenders over a runtime check.

---

### D-011: `nomic-embed-text` not yet installed in Ollama

**Observation (recorded, not a decision):** The system's Ollama has
`qwen2.5:14b`, `gemma4:e4b`, `whiterabbitneo`, and `dolphin-mistral`.
`nomic-embed-text` is absent.

**Action:** `nx doctor` reports FAIL for this check with the fix hint
`ollama pull nomic-embed-text`. This will be pulled as part of M4 (memory)
setup when embeddings are first needed.

---

### D-012: `nx doctor` gate mode check always PASS

**Decision:** The gate mode check in `nx doctor` is informational (always
PASS) — it shows the configured mode but does not fail if mode is "off".

**Rationale:** "off" is a valid mode. The doctor should report what mode
is configured, not demand a particular mode.

---

## M1 — Models and Router

### D-013: Protocol signature for `chat` as an async generator

**Decision:** In `Provider(Protocol)`, declare `def chat(...) -> AsyncIterator[ChatChunk]: ...`
rather than `async def`.

**Rationale:** Python async generator functions yield items asynchronously and return
an `AsyncIterator[T]`. Declaring `async def` in the typing Protocol would imply a
coroutine returning an iterator (`Coroutine[..., AsyncIterator]`), which causes type
mismatches under strict `mypy`.

---

### D-014: Direct `httpx` HTTP clients for Ollama and NVIDIA

**Decision:** Implement `OllamaProvider` and `NvidiaProvider` using `httpx.AsyncClient`
directly rather than wrapping heavy external SDKs.

**Rationale:** The Ollama and OpenAI-compatible endpoints are standard REST APIs.
Using `httpx` gives full, granular control over timeouts, streaming line parsing,
token-bucket rate limiting, jittered retry backoff, and exact error conversion without
library version friction.

---

### D-015: Token-bucket rate limiter for NVIDIA API

**Decision:** Use an async token-bucket algorithm using `time.monotonic()` and an
`asyncio.Lock` to enforce the configured `requests_per_minute` (default 30).

**Rationale:** Staying under NVIDIA's ~40 RPM free tier requires precise rate
spacing. The token-bucket allows short bursts while guaranteeing the average rate
never exceeds the limit, and callers sleep only the exact fractional duration required.

---

### D-016: Privacy enforcement strictly in code at router boundary

**Decision:** `ModelRouter.check_privacy` raises `PrivacyViolation` immediately if any
`Message` has `PRIVATE` taint and the target provider is non-local (`is_local=False`).

**Rationale:** Non-negotiable Principle 4 ("Local-first, privacy-enforced"). Private
data must never reach cloud endpoints, and this rule is enforced before any HTTP client
or network socket is initialized.

---

### D-017: Transparent cloud fallback to local `brain`

**Decision:** If a call to the cloud role `heavy` fails (offline, rate limited, 5xx),
the router automatically re-routes the request to the local `brain` role and calls
`on_notice` with an explanatory message.

**Rationale:** Principle 7 ("Fail soft"). Cloud unavailability should degrade
gracefully rather than aborting the owner's task.

---

### D-018: Two-tier shell command safety evaluation

**Decision:** Evaluate shell commands first by scanning raw string patterns for destructive
operations (fork bombs, `rm -rf`, disk wipes, `sudo`, `curl|sh`), then parse `argv` via
`shlex.split` to inspect binary allowlists, subcommands (e.g. `pacman -Q*` vs `-S*`), and
sensitive path tokens (`.ssh`, `secrets.env`, `/etc/sudoers`).

**Rationale:** Attackers or prompt injections may attempt to hide arguments in subshells or
chaining. Scanning both the raw command line and the parsed arguments guarantees that neither
pipes/operators nor token tricks can bypass the safety tiering.

---

### D-019: Path validation with symlink resolution and explicit root whitelisting

**Decision:** Resolve all filesystem paths via `Path.resolve()` prior to boundary checks.
Explicitly check `denied_paths` before `allowed_file_roots`.

**Rationale:** Symlink traversal attacks can point inside an allowed root to a restricted
target (e.g. `~/.ssh`). Resolving the path to its physical target before validation ensures
no symlink escapes are possible.

---

### D-020: SSRF protection with redirect validation and NAT64 translation

**Decision:** Enforce pre-flight DNS and IP checks blocking loopback, private RFC1918,
carrier-grade NAT, and cloud metadata (`169.254.169.254`). For IPv6 NAT64 prefix `64:ff9b::/96`,
extract the embedded IPv4 address and evaluate it against private ranges. In addition, validate
every redirect URL in HTTP clients individually.

**Rationale:** Web fetchers must prevent local network pivot attacks or cloud metadata
exfiltration, even in networks utilizing DNS64/NAT64 or HTTP redirect chains.

---

### D-021: Taint-based approval escalation for tool execution

**Decision:** In `ToolRegistry.execute`, inspect the caller's `context_taint`. If
`TaintFlag.UNTRUSTED` is present in the context (from web fetching or external file reading),
any `write` or `dangerous` tool execution requires explicit owner approval regardless of the tool's
base tier.

**Rationale:** Indirect prompt injection mitigation. If an untrusted web page or document contains
hidden instructions to modify files or run commands, the agent cannot execute write operations
without owner confirmation.

---

### D-022: Output truncation and secret redaction

**Decision:** Truncate all tool outputs to 6,000 characters (or 200 KB for raw file reads) and
pass them through regex secret redaction for API keys (`nvapi-*`, `sk-*`, `ghp_*`, `Bearer *`).

**Rationale:** Prevents context window exhaustion, denial of service from huge outputs, and
accidental token leakage in audit logs or LLM context.

