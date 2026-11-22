NoX Session M: Port the Useful Parts of OpenJarvis, Then Delete It
Save this file as docs/prompts/session-M.md in the NoX repo and give it, whole, to the coding agent. It is written for an agent that must not guess: every step says what to do, how to check it, and what proof to save.
0. Mission
You are the coding agent for NoX, a personal AI agent for the owner's Arch Linux machine (Hyprland desktop). The owner goes by Adhi. Your job in this session:
1.  Study the open-source project OpenJarvis (Stanford, Apache-2.0) by cloning it into a temporary folder.
2.  Port a small, chosen set of its ideas and code into NoX's own package (noxai), written as NoX code, in NoX's style.
3.  Prove each port works with real evidence.
4.  Delete the OpenJarvis clone completely when finished, keeping only the license attribution that the Apache-2.0 license requires.
OpenJarvis is a reference, never a dependency. After this session NoX must run with OpenJarvis not installed, not imported and not present on disk (except attribution text).
What NoX is (as described by the owner; VERIFY every line against the repo before relying on it)
• Package noxai, CLI nx, project at ~/Documents/Projects/NoX. Python. Arch Linux only for now.
• Direction set in Session K: an always-on daemon (systemd user service) plus a Tauri desktop shell with a tray, packaged by a PKGBUILD so no venv or uv is needed by the user. Session L added a daily GitHub backup job and job planning/notification work. You do not know whether K and L are fully done. Section 3 makes you find out.
• Hybrid models: chat replies via the NVIDIA API for speed; scheduling and background work on a small always-running local model; an automatic intent check on every request (quick reply, reminder or schedule, important task).
• Three NVIDIA API keys with a key-pool algorithm: one key pinned to a running background job, work split across keys when idle.
• The local model qwen2.5:14b runs at about 2 tokens per second on this machine (CPU-bound), so local replies take 20 seconds or more. Do not design anything that needs a large local model.
• Daily briefing: two automatic runs a day (about 04:00 and 18:00 IST, with a catch-up run on login if the time passed), independent of the UI, with desktop notifications; each topic researched as its own task, then combined into a formatted briefing with source links; like/dislike per topic with free-text feedback; dislike should make NoX rewrite its own briefing prompt.
• Known defects reported by the owner: NoX forgets earlier turns of the same chat; scheduled briefings did not fire and the manual run did not work (as of Session I); when the cloud model was decommissioned (HTTP 410) chat silently fell back to the slow local model.
• Earlier agent reports overclaimed (coverage numbers, tags, tests that only used mocks). The owner re-checks your report with his own commands. Assume he will.
Workstreams, in this order
ID
Name
Why
A
Engine health and loud failure
Stops silent fallback; fixes the HTTP 410 class of failure
B
Persistent scheduler pattern
Makes briefings and backups fire without the UI
C
Trace logging
Gives the dislike-rewrites-prompt feature real data
D
Memory: documents and conversation
Fixes the forgotten-chat problem; adds search over Obsidian and Study notes
E
Skills catalog and MCP tools
Extensibility without hardcoding each feature
F
Cleanup
Delete the OpenJarvis clone, leave attribution
Do them in this order, one at a time. A workstream is finished only when its acceptance checks (listed inside it) have passed and their proof is saved. Do not start the next workstream with the previous one half-done.
Explicit non-goals (do NOT do these)
• Do not install OpenJarvis (pip, uv add, uv sync of its repo, maturin, its installers, or any curl | bash). You only read its files.
• Do not build or depend on its Rust extension.
• Do not port or run any model-weight training (SFT, GRPO, distillation).
• Do not import its desktop GUI, web UI, installers or its engine management.
• Do not add messaging channels (Discord and others) in this session.
• Do not rewrite NoX's UI, FaceLock integration, key-pool algorithm or intent check, except for the exact integration points named below.
• Do not run any model larger than a few billion parameters locally.
1. Operating rules (non-negotiable)
1.1 Evidence protocol
The owner does not accept claims. Every statement in your final report needs a command and its real output.
• Create docs/reports/session-M/ and put every proof there as text files (command on the first line, output below it).
• Any number you report (tests passed, coverage, latency) must be copied from a tool's output saved in that folder. Never estimate. Never round up.
• Mark anything you did not run yourself as UNVERIFIED. Mark anything you could not make work as FAILED and say why. A report with honest FAILED lines is good; a report with false success is a defect.
• A mock-only test does not prove a feature. For each workstream, at least one proof must exercise the real code path with real files, a real SQLite database and a real process or clock. If a part needs a live external service (NVIDIA API, a real keypair), run it once for real and save the output with secrets redacted; if you truly cannot, say UNVERIFIED (needs live key).
• Never print, log or commit API keys. Redact them in every saved output (replace with nvapi-REDACTED).
1.2 No fakes, no stubs left behind
• No TODO, pass, placeholder returns, or functions that only log a message. If something is out of scope, do not create a stub for it.
• No feature flag that defaults to off for something the owner asked to be on.
• Do not mark a task done because code exists. Done means: acceptance check passed and proof saved.
1.3 Git discipline
• Work on a new branch session-m created from the current main branch. Never commit to main directly.
• One commit per logical task, with a message that says what and why. Run the test suite before each commit.
• Do not force-push, rewrite history, or delete branches.
• Do not merge to main. Leave the branch for the owner to review.
1.4 Safety for destructive commands
• The only deletion you may do outside the NoX repo is removing the OpenJarvis clone in Section 9, with the exact guarded procedure given there.
• Never run rm -rf on a path built from a variable you did not print and check first. Never run it on ~, /, the repo root or any parent of the repo.
• Do not touch ~/Documents/Certificates, ~/Documents/Study, or the Obsidian vault except to read for the memory feature, and only through the read-only indexer (Workstream D). Never modify or delete files there.
• Do not modify system files, systemd units outside the NoX user units, or Hyprland config.
1.5 Do not regress earlier work
• Before changing anything, run the full existing test suite and save the result as the baseline in docs/reports/session-M/baseline-tests.txt.
• After every workstream, run it again. The number of failing tests must not increase. If a test fails because of your change, fix your change, not the test, unless the test was provably wrong, in which case explain in the report.
1.6 Scope discipline
• Port the smallest amount of OpenJarvis code and the largest amount of understanding. Prefer re-implementing a pattern in about 100 to 300 lines of clean NoX code over copying a large module with its dependencies.
• If a file you want to port pulls in many OpenJarvis internals, do not copy the tree. Re-implement the idea.
• Match NoX's existing style: its logging, config system, error types, typing, naming, test layout. Read before writing.
1.7 When you are blocked
If an instruction conflicts with what you find in the repo, do not improvise silently. Write the conflict into docs/reports/session-M/questions.md, choose the safest reversible option, do it, and flag it in the report. Never invent file paths, function names or config keys: look them up.
2. Tooling and environment checks
Run and save the output of each:
cd ~/Documents/Projects/NoX
git status --short
git branch --show-current
git log --oneline -15
python --version
uv --version 2>/dev/null || echo 'uv not installed'
ollama list
systemctl --user list-units 'nox*' --no-pager
systemctl --user status nox* --no-pager 2>&1 | head -40
df -h ~ | tail -2
free -h
nproc
Then create the branch:
git checkout -b session-m
If git status shows uncommitted changes from earlier work, do not discard them. Commit them on session-m as a separate commit named wip: pre-session-M state and note it in the report.
3. Phase 0: Recon (read-only, no feature code yet)
You will produce two documents before writing any feature code. Do not skip this. It is what keeps you from building on wrong assumptions.
3.1 Audit NoX: write docs/reports/session-M/00-nox-audit.md
Answer every question below with file paths and short evidence. If the answer is 'not found', say so.
1.  Repo layout: print the tree two levels deep (ignore .git, node_modules, .venv, target). Identify where each of these lives: the daemon entry point, the CLI, the web API server, the Tauri shell, the agent loop, the model/engine client code, the key pool, the intent check, the briefing code, the job/task system, the memory code, the config loader, the notification code, the tests.
2.  Language, Python version and dependency manager actually used. List direct dependencies.
3.  Config: where is configuration read from and in what format? Where are the NVIDIA keys stored? (Report only the location, never the values.)
4.  Storage: what databases or files does NoX persist (paths, formats)? Is SQLite already used? Which tables exist? Print schema with sqlite3 <db> .schema for each database.
5.  Engines: how does NoX call the NVIDIA API and the local Ollama model? Where is the fallback from cloud to local implemented? Find the exact code that falls back silently and quote the function name and line range.
6.  Scheduling: how are briefings and the backup job triggered today? Is there a daemon-owned scheduler, a systemd timer, a thread inside the UI process, or something else? Why would a run get cancelled when the UI opens (find the actual cause in code, do not guess)?
7.  Conversation memory: how is chat history stored and how is it passed to the model per turn? Find exactly why earlier turns are lost (truncation, no history passed, per-request new session, token limit). Quote the lines.
8.  Briefing feedback: where are like/dislike and the free-text 'what to improve' stored, and where is the briefing prompt that dislike feedback should rewrite?
9.  Sessions J, K, L status: for each of the following, state DONE (with proof command output), PARTIAL or MISSING: standalone daemon as a systemd user service; Tauri tray app; PKGBUILD; one 'Run briefing' button; automatic 04:00 and 18:00 runs with login catch-up; cloud connection ON by default; the 20:00 IST GitHub backup job with a boot-time catch-up; job pre-planning with the cloud key; completion and error notifications; delete buttons for jobs, tasks and briefings. Run the real commands that prove each one (for example systemctl --user is-active, a real dry run, journalctl --user -u <unit> -n 50).
10.  Tests: run the existing suite, report pass/fail counts from the tool output, and how coverage is measured (if at all).
End the audit with a section 'Integration points': a table listing, for each workstream A to E, the exact existing files and functions you will modify or extend.
3.2 Clone OpenJarvis for reading
Use a temporary folder outside the NoX repo. Print and verify the path before use.
OJ_DIR="$(mktemp -d /tmp/openjarvis-study.XXXXXX)"
echo "$OJ_DIR"
cd "$OJ_DIR"
git clone --depth 1 https://github.com/open-jarvis/OpenJarvis.git src
cd src
git rev-parse HEAD
git log -1 --format='%H %cd'
cat LICENSE | head -20
ls
Rules:
• Use only the official repository open-jarvis/OpenJarvis. Several forks exist on GitHub with similar names. Do not clone any fork.
• Record the full commit hash and the date in docs/reports/session-M/01-openjarvis-study.md and in THIRD_PARTY_NOTICES.md (Section 4).
• Do not run its scripts, installers, tests, or uv sync. Read files only.
• Confirm the license file is Apache-2.0 before porting anything. If it is not, stop and report.
3.3 Study OpenJarvis: write docs/reports/session-M/01-openjarvis-study.md
Read the following areas in the clone. Paths come from its published architecture notes; verify they exist and adjust if the layout differs.
• src/openjarvis/engine/: the inference engine interface (generate, stream, list_models, health) and the cloud backend.
• src/openjarvis/scheduler/: cron and interval task scheduling and its MCP scheduler tools.
• src/openjarvis/memory/: memory backends (SQLite, BM25, FAISS, ColBERT), document ingestion, chunking and context injection with source attribution.
• src/openjarvis/agents/: the base agent class, the registry, and especially the continuous monitor agent with memory compression and the deep-research agent with inline citations.
• src/openjarvis/learning/ and the traces module: how traces are recorded and what fields they hold (read only the recording, not the training).
• src/openjarvis/mcp/ and the skills code (search for skill, SKILL.md, agentskills): how skills are discovered from a catalog, loaded and invoked as tools.
• src/openjarvis/tools/: the tool interface and registry.
• docs/: architecture overview, skills guide, memory guide, scheduler guide.
For each area write: what it does in your own words (not pasted code), file sizes in lines, its imports from other OpenJarvis modules, license headers present, the specific functions or classes worth porting, and what NoX needs instead of copying (because of dependencies). Finish with a 'Port decision' table: item, port as code or re-implement as pattern, estimated lines, risk.
Gate: if the study shows that a workstream below depends on something that does not exist in OpenJarvis (for example no scheduler persistence), say so and build the NoX version from first principles using the acceptance checks, still crediting nothing you did not take.
4. Licensing and attribution (Apache-2.0)
OpenJarvis is Apache License 2.0. You may reuse and adapt its code if you follow these rules. Do them as you go, not at the end.
1.  Create THIRD_PARTY_NOTICES.md at the repo root. Add an entry for OpenJarvis with: project name, official URL, the commit hash you studied, license name, and a table listing each NoX file that contains code copied or closely adapted from it (file path, which OpenJarvis file it came from, what was changed).
2.  Create third_party/openjarvis/LICENSE containing the full Apache-2.0 text copied from the clone, and third_party/openjarvis/NOTICE if the repo has a NOTICE file (copy it verbatim).
3.  Every NoX file that contains copied or closely adapted OpenJarvis code gets a header comment: 'Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0), commit <hash>. Modified for NoX: <one line>.'
4.  Files that only re-implement an idea from scratch need no header, but you must still list the idea in THIRD_PARTY_NOTICES.md under 'Concepts referenced'.
5.  Never copy text from its documentation or README into NoX docs. Write your own.
6.  The attribution files in steps 1 to 3 stay in the repo after the clone is deleted in Section 9.
5. Workstream A: Engine health and loud failure
5.1 Problem
When the cloud model was decommissioned (HTTP 410), NoX silently fell back to the local model, which is about 2 tokens per second. The owner got 20-second replies with no explanation. NoX must detect an unhealthy engine, say so clearly, and never degrade silently.
5.2 What to build
Introduce a small engine abstraction in noxai (adapt the existing client code; do not rewrite working call paths wholesale). Study OpenJarvis's engine interface (generate, stream, list_models, health) and take the idea, not the multi-engine zoo.
Requirements:
1.  An Engine interface with at least health(), list_models(), generate() and stream(), with two concrete engines: NvidiaEngine (OpenAI-compatible NVIDIA endpoint, using the existing key pool) and OllamaEngine. Wrap the existing code paths so behavior is unchanged when healthy.
2.  health() returns a typed result: status (ok, degraded, down), reason code (auth, model_gone, rate_limited, network, timeout, unknown), a human sentence, the time checked, and latency in milliseconds. HTTP 401 or 403 map to auth; 404 or 410 on the configured model map to model_gone; 429 to rate_limited; connection errors to network.
3.  Model validation at daemon start and then every 10 minutes: confirm each configured model name appears in list_models() for its engine. A configured model that is missing marks that engine down with reason model_gone.
4.  Explicit fallback policy in config with three modes: ask (default: do not answer with local, tell the user the cloud is down and offer to use local), auto_notify (fall back and tell the user in the reply and in a notification), never. The old silent behavior must not exist anywhere. Search the codebase for every place that catches an exception and switches model; each must go through the policy.
5.  Every reply that came from a fallback model carries visible metadata: served_by, reason, and the UI shows a banner. The CLI prints a one-line notice.
6.  A desktop notification (reuse the existing notification code) when an engine goes from ok to down or degraded, rate-limited to one per engine per hour, and one when it recovers.
7.  A command nx doctor that prints the health of every engine, every configured model, the key pool (count of keys and which are cooling down, never the keys), the scheduler state (after Workstream B) and the memory index (after Workstream D). Exit code non-zero if anything is down.
8.  The key pool must keep its current behavior. Health checks use one lightweight call and must not consume the pinned background key: read the pool's API to take a key the same way a normal request does, and respect pinning.
5.3 Acceptance checks (all must pass, proof saved)
• A1: nx doctor on the healthy system shows ok for each engine with real latency.
• A2: Set the cloud model name to a nonexistent value in a temporary config copy. Restart the daemon with that config. nx doctor shows NvidiaEngine down (model_gone); a notification fires; a chat request under policy ask returns the 'cloud is down, use local?' message and does NOT call Ollama (prove with Ollama's log or a request counter).
• A3: Policy auto_notify: same broken config; chat reply is served by Ollama, carries served_by=ollama with the reason, and a notification fired.
• A4: Real network failure: block the NVIDIA host for the daemon (for example with a temporary invalid base URL in the temp config, never by changing system firewall rules) and show network reason.
• A5: Unit tests for each reason-code mapping using recorded real error bodies, plus the integration proof above. Save the pytest output.
• A6: Grep proof: grep -rn showing no remaining except-block that switches engine without going through the policy.
Commit message prefix: feat(engine):.
6. Workstream B: Persistent scheduler
6.1 Problem
Briefings are supposed to run at about 04:00 and 18:00 IST and catch up on login, in the background, whether or not the UI is open. The owner reports they do not fire, the manual run button does nothing, and runs get cancelled when the UI opens or he does something else. The backup job is supposed to run daily after 20:00 IST with a boot-time catch-up.
6.2 What to build
A daemon-owned scheduler. Study OpenJarvis's scheduler (cron and interval tasks). Port the idea of persisted jobs with a clear state machine; implement it on NoX's storage.
Requirements:
1.  Persistence: a SQLite table schedules (id, name, kind cron or interval, expression, timezone default Asia/Kolkata, enabled, last_run_at, next_run_at, last_status, catchup_policy, created_at) and schedule_runs (id, schedule_id, started_at, finished_at, status running|success|failed|cancelled|missed, error, trace_id, log_path). Use NoX's existing database file and migration approach; do not invent a second database unless the audit shows no database exists.
2.  The scheduler loop lives in the daemon process only. The UI, CLI and Tauri shell talk to it through the daemon's existing API. Opening or closing the UI must never start, stop or cancel a scheduled run. Find the code that currently ties runs to the UI and remove that coupling.
3.  Time: always use timezone-aware datetimes. Compute next run from the schedule's timezone. Handle IST having no daylight saving, but write the code so another timezone would also work.
4.  Catch-up: on daemon start and on resume from suspend (detect a clock jump greater than 2 minutes between loop ticks), for each enabled schedule whose next_run_at is in the past, apply catchup_policy: run_once (default; run one catch-up and then set next_run_at to the next future slot), skip, or run_all. Mark the missed slot in schedule_runs as missed when skipped.
5.  Single flight: a schedule never has two runs at once. If a run is still running after its timeout, mark it failed with reason timeout and notify. Add a heartbeat so that a run left in running after a crash is detected on next start and marked failed with reason interrupted. (The owner's backup job once sat in running forever; this must be impossible.)
6.  Defaults to create, idempotently: briefing_morning at 04:00 IST, briefing_evening at 18:00 IST (both call the same briefing function), backup_daily at 20:00 IST with catch-up run_once triggered at daemon start. If the backup schedule already exists from Session L, adopt and migrate it instead of duplicating.
7.  One manual trigger: nx schedule run <name> and the UI's single 'Run briefing' button both call the same daemon API; the run executes in the daemon, survives the UI closing or refreshing, and reports progress through the existing events channel.
8.  Notifications: on run start (optional, off by default), on success, on failure with the error summary, and when a run was missed and caught up.
9.  CLI: nx schedule list|add|remove|enable|disable|run|history. A natural-language edit path is out of scope here; the data model must make it possible later (keep prompt text for the briefing in the database or a file the scheduler reads, not hardcoded in the schedule).
10.  Deletion: add delete operations for jobs, tasks and briefings if Session L did not complete them (check the audit). Deleting a running job must cancel it cleanly first.
11.  Time source injectable for tests (a Clock object), with one real wall-clock proof as well.
6.3 Acceptance checks
• B1: Unit tests with a fake clock: next-run calculation across midnight, catch-up policies, single-flight, interrupted-run recovery. Save pytest output.
• B2: Real-time proof: create a schedule that fires every 2 minutes, start the daemon with the UI NOT running, wait for three real firings, and save schedule_runs rows plus journalctl --user lines showing them.
• B3: UI independence: during a long running job, start and kill the UI shell; show the run still finishing with success.
• B4: Catch-up proof: stop the daemon, set a schedule's next_run_at to one hour ago in the database, start the daemon, and show exactly one catch-up run and a correct new next_run_at.
• B5: Crash proof: start a long run, kill -9 the daemon, restart, and show the run marked failed (interrupted) plus a notification.
• B6: Show nx schedule list with the three default schedules and their next run times in IST.
Commit message prefix: feat(scheduler):.
7. Workstream C: Trace logging
7.1 Problem
The owner wants dislike feedback on a briefing topic to make NoX rewrite its own briefing prompt. That only works if every run records what was asked, what was used and what the outcome was. OpenJarvis records traces of agent runs and uses them for learning. Take the recording idea only. Do not port any training or policy-learning code.
7.2 What to build
1.  A traces table and a small recorder in noxai, written to NoX's existing database. Fields: id, started_at, finished_at, source (chat, briefing, job, schedule), schedule_run_id (nullable), request_text (truncated to 4,000 characters), intent label from the existing intent check, engine and model that served it, key slot index (never the key), steps (JSON array of: step name, tool or skill used, duration in milliseconds, status), token counts if the provider returns them, final status, error text, and feedback (JSON: like or dislike, free text, topic id, timestamp).
2.  Redaction before storage: strip anything matching API key patterns (nvapi- followed by characters, long hex or base64 tokens over 32 characters, Authorization: header values, GitHub tokens). Write a unit test with real-looking fake secrets proving they never reach the database.
3.  Retention: keep 90 days by default (config), delete older rows daily through the scheduler from Workstream B (a built-in traces_cleanup schedule). Cap total rows at 100,000.
4.  Hook points: the chat handler, the briefing runner (one trace per topic task plus one for the combine step), the job runner, and every scheduled run. Use a context-manager style helper so a crash still closes the trace with status failed.
5.  Briefing feedback link: when the user dislikes a topic with a note, store the feedback on the trace of that topic's last run, and create a record in a prompt_feedback table (topic, trace_id, note, created_at, applied boolean). Then implement the rewrite step: a function improve_topic_prompt(topic) that reads the current prompt text for the topic, the last 5 disliked traces and their notes, asks the cloud model (through the engine layer, so health and policy apply) for a revised prompt, validates it (non-empty, under a length limit, still mentions the topic, no secrets), saves the old prompt as a version row, saves the new one as active, and marks the feedback applied. Provide nx prompt history <topic> and nx prompt rollback <topic> <version> so a bad rewrite can be undone.
6.  A read-only CLI: nx traces list --last 20, nx traces show <id>, nx traces stats --days 7 (counts by source and status, median duration, fallback count).
7.3 Acceptance checks
• C1: Run a real briefing for one topic. Show the resulting trace rows (steps, durations, engine, status) with secrets absent.
• C2: Redaction test output proving fake key strings never appear in the database file (strings or a SELECT on the raw tables).
• C3: Dislike a topic through the same API the UI uses, with a note; show the prompt_feedback row, then run improve_topic_prompt, and show the old and new prompt side by side, the version rows and the applied flag. Then run nx prompt rollback and show the old prompt active again.
• C4: Crash proof: kill the daemon mid-run; after restart the trace shows failed (or interrupted), not an open row.
• C5: Retention proof: insert backdated rows, run the cleanup schedule, show them gone.
Commit message prefix: feat(traces):.
8. Workstream D: Memory (documents and conversation)
There are two separate memory problems. Solve them with one shared storage layer but two separate APIs and two separate sets of tests.
8.1 D1: Conversation memory (fixes the forgotten-chat defect)
Problem. NoX does not remember earlier turns of the same chat. The owner asks about a topic, continues later, and the thread is lost.
First, find the cause. In your audit (Section 3.1, question 7) you identified exactly why. Fix that cause first (for example history never passed, a new session per request, or truncation to the last turn). Write a failing test that reproduces the bug on the current code, then fix it. Do not skip this even if a fancier design follows.
Then, build the memory algorithm. Study OpenJarvis's continuous monitor agent and its memory compression, then implement this design in NoX (adjust only if the audit shows a constraint):
1.  Store every turn in a messages table (conversation_id, role, text, created_at, token_estimate, turn_index).
2.  Build the model context for each request from four parts, in this order, within a token budget taken from the active model's context limit (leave 25 percent for the answer):
    ◦ the system prompt and any pinned facts;
    ◦ a rolling summary of older turns (see step 3);
    ◦ the most recent N turns verbatim (start with the last 8 turns, shrink automatically if over budget);
    ◦ up to K retrieved older snippets relevant to the new message (see step 4), each tagged with its turn index.
3.  Rolling summary: when verbatim history exceeds 60 percent of the budget, summarize the oldest turns that are not in the verbatim window into a compact summary and store it (conversation_summaries: conversation_id, covers_turn_range, text, created_at). The summary must keep names, decisions, numbers, file paths, commands and open questions. Run summarization in the background on the small local model or the cloud model per the intent check, never blocking the reply. If summarization fails, keep the old summary and record a trace.
4.  Retrieval over past turns: use the shared search layer in D2 (SQLite FTS5 BM25) over the messages table for the current conversation, and optionally across conversations when the user's message contains a recall phrase such as 'earlier', 'last time', 'we talked about'.
5.  Topic continuity: store a short topic_label per conversation segment so that returning to an earlier topic (after talking about something else) pulls its summary and snippets back in.
6.  Never let summarization or retrieval raise an exception to the user. On failure, fall back to verbatim recent turns and record the error in the trace.
Acceptance checks for D1:
• D1-a: A regression test and a real run: tell NoX three distinct facts in separate turns, ask about them 15 turns later, and show correct answers. Save the transcript.
• D1-b: Topic switch test: discuss topic X for 10 turns, topic Y for 20 turns, then ask 'what was that command we used in X'. Show it answered correctly with the turn reference.
• D1-c: Budget proof: log the assembled context size for a long conversation; show it never exceeds the budget and that the summary row exists.
• D1-d: Kill and restart the daemon between turns; the conversation continues with memory intact.
8.2 D2: Document memory (search over notes)
Study OpenJarvis's memory ingestion (chunking, embedding, source attribution, backend interface). Build the NoX version:
1.  Backend: SQLite with FTS5 for BM25 keyword search. This needs no extra dependency and is light on the CPU. Table design: documents (id, path, mtime, size, sha256, indexed_at, source_root), chunks (id, document_id, chunk_index, text, heading_path, char_start, char_end), and an FTS5 virtual table over chunk text.
2.  Optional embeddings: a feature flag memory.embeddings.enabled, default OFF. If enabled, use a small embedding model served by the local Ollama (the owner picks the model in config; do not pull a model automatically) and store vectors in a separate table; hybrid ranking combines BM25 and cosine similarity with reciprocal rank fusion. Provide the code, test it with a tiny model if the owner has one installed, otherwise report UNVERIFIED (no embedding model installed) for the embeddings part. Do not block the workstream on it.
3.  Sources: configurable list of read-only roots. Defaults: the Obsidian vault (~/Documents/Obsidian Vault) and ~/Documents/Study. Do NOT index ~/Documents/Certificates by default. Index only text-like files (.md, .txt, .rst, .org, source code, and .pdf only if a PDF text extractor already exists in NoX's dependencies; do not add heavy dependencies). Skip hidden folders, .obsidian, .git, files over 2 MB.
4.  Chunking: split Markdown by headings first, then by paragraphs to about 800 characters with 100 characters of overlap; keep the heading path for attribution.
5.  Incremental indexing: on daemon start and on a schedule (use Workstream B, default every 30 minutes) compare mtime and sha256, re-index changed files, remove deleted ones. A file watcher is optional; do not add one if it needs a new dependency.
6.  Read-only guarantee: the indexer opens files read-only and never writes into the source roots. Add a test that makes the root read-only (a temporary copy, never the real folders) and verifies indexing still works.
7.  Query API: search(query, k=5, roots=None) returns chunks with path, heading path, a snippet, score and line range. The chat flow injects retrieved chunks into the context only when the intent check or the user's phrasing suggests the notes are relevant (for example 'in my notes', 'in my vault', 'what did I write about'), each with its source path, and the reply must cite the source files it used.
8.  CLI: nx memory index, nx memory search "<query>", nx memory stats, nx memory forget <path>.
9.  Privacy: show in the UI settings (or at minimum in nx memory stats) which roots are indexed. Provide nx memory roots add|remove.
Acceptance checks for D2:
• D2-a: Index the real vault and Study folder. Save nx memory stats (document count, chunk count, index size, duration). Report real numbers only.
• D2-b: Three real queries from the owner's content (pick distinctive phrases from three real notes), each returning the right file at rank 1 to 3 with a correct snippet.
• D2-c: Change a file in a temporary copy of a root, re-index, and show the change found; delete it and show it gone.
• D2-d: Chat proof: ask 'what did I write about <real topic> in my notes' and show the reply citing real file paths.
• D2-e: Show that file checksums of the real roots are unchanged before and after indexing (find ... -type f -exec sha256sum before and after, diff is empty).
Commit message prefixes: feat(memory-chat): and feat(memory-docs):.
9. Workstream E: Skills catalog and MCP tools
9.1 Goal
Let NoX discover and use reusable 'skills' (folders of instructions) and external tools over MCP without hardcoding each capability. Study how OpenJarvis discovers a catalog of skills, exposes each skill as a tool to the agent, and follows the open agentskills.io format (a folder with a SKILL.md that has YAML frontmatter with at least name and description). Read the actual format in the OpenJarvis code and docs; do not assume.
9.2 What to build
1.  Skill loader: a directory ~/.local/share/nox/skills/ (confirm the right data directory from the audit) and a repo-level skills/ for shipped skills. Each skill is a folder with SKILL.md. Parse frontmatter safely (YAML safe loader). Validate: name matches folder, description present and under 1,024 characters, size under 100 KB, no symlinks pointing outside the folder.
2.  Catalog and discovery: build an in-memory index of skills with name and description only. The agent sees the list of names and descriptions in its prompt (kept short), and loads a skill's full body only when it decides to use it (progressive loading). Provide nx skill list|show|enable|disable|install|remove.
3.  Safety (important): skills are untrusted text. By default a skill can only add instructions; it cannot run code. If a skill folder contains scripts, they are NOT executable by the agent unless the owner explicitly enables skills.allow_scripts for that named skill and approves each run in the UI or CLI. Never auto-install from a remote catalog. nx skill install <git-url-or-path> clones or copies into a staging folder, shows the owner the SKILL.md and a file listing, and requires typed confirmation. Imported skills start disabled.
4.  Ship two small, useful, hand-written skills to prove the system: briefing-format (the formatting rules for NoX briefings, with clickable source links) and notes-search (how to use the document memory and cite sources). Write them yourself; do not copy any skill from OpenJarvis or its community catalogs.
5.  MCP client: study OpenJarvis's MCP layer. In NoX, implement a minimal MCP client that can connect to servers listed in config (stdio transport first), list their tools, and expose them to the agent with the same safety model: each server is disabled until the owner enables it; each tool call is logged to traces; a per-server allowlist of tool names; a timeout of 30 seconds per call. If NoX already has an MCP client, extend it instead of writing a second one. If adding MCP needs a new dependency, prefer the official MCP Python SDK; justify it in the report and add it to the PKGBUILD dependency list.
6.  Integration with the intent check: add a branch so that when the user's request matches an enabled skill's description, the agent loads that skill. Keep the intent check's existing outputs working.
7.  UI: a minimal settings page or panel listing skills and MCP servers with enable/disable toggles, using NoX's existing UI components and the night theme. Do not redesign anything else.
9.3 Acceptance checks
• E1: nx skill list shows the two shipped skills. A chat request that matches notes-search loads it (show the trace step).
• E2: Validation tests: a malformed frontmatter, an oversized file and an escaping symlink are rejected, with the pytest output saved.
• E3: Install test: create a local test skill folder in /tmp, run nx skill install, show the confirmation prompt, confirm, and show it listed as disabled; enable it and use it.
• E4: Script safety: a skill containing a script cannot execute it with the default config (show the refusal).
• E5: MCP proof: connect to one real local MCP server (for example the reference filesystem server restricted to a temporary folder) if it can be run without new system packages; show the tool list and one real tool call in a trace. If you cannot run a real server, say UNVERIFIED (no MCP server available) and prove the protocol handling against a minimal fake stdio server you write in the tests, clearly labeled as such.
Commit message prefix: feat(skills): and feat(mcp):.
10. Workstream F: Delete the OpenJarvis clone (cleanup)
Do this last, after every acceptance check has passed or has been recorded as UNVERIFIED or FAILED with a reason.
1.  Confirm nothing in NoX imports or references the clone: run grep -rn "openjarvis" --include=*.py --include=*.toml --include=*.json --include=*.md --include=PKGBUILD . | grep -v third_party | grep -v THIRD_PARTY_NOTICES | grep -v docs/reports and save the output. Every remaining hit must be an attribution header comment. Remove any other hit.
2.  Confirm the NoX test suite passes with the clone not on the Python path.
3.  Delete the clone with this guarded procedure and save the output:
echo "$OJ_DIR"                    # must print /tmp/openjarvis-study.XXXXXX
case "$OJ_DIR" in
  /tmp/openjarvis-study.*) ls "$OJ_DIR" && rm -rf -- "$OJ_DIR" ;;
  *) echo 'REFUSING: unexpected path' ; exit 1 ;;
esac
[ -e "$OJ_DIR" ] && echo 'STILL EXISTS' || echo 'DELETED'
find / -maxdepth 4 -iname '*openjarvis*' -not -path '*/proc/*' 2>/dev/null | head
If $OJ_DIR is no longer set in your shell, find the folder with ls -d /tmp/openjarvis-study.*, print it, check it is the only match, and then delete it with the same guard.
4.  Confirm nothing was installed system-wide: pip list 2>/dev/null | grep -i jarvis, uv pip list 2>/dev/null | grep -i jarvis, ls ~/.cache | grep -i jarvis, ls ~/.local/share | grep -i jarvis, and ollama list unchanged from Section 2. Save outputs.
5.  Keep only: THIRD_PARTY_NOTICES.md, third_party/openjarvis/LICENSE (and NOTICE if it existed), the attribution header comments, and your own study document in docs/reports/session-M/.
11. Packaging and documentation
• Update the PKGBUILD dependency lists for any new dependency; build and install the package in a clean way if Session K produced one, and show nx doctor working from the packaged install. If K is incomplete, record it as UNVERIFIED and do not attempt to finish K inside this session.
• Update the NoX README or docs with: how the scheduler works, how conversation memory works, how to add a memory root, how to add a skill, how to enable an MCP server, the fallback policy options, and the nx commands added.
• Add docs/prompts/session-M.md (this file) to the repo if it is not already there.
12. Verification matrix (fill this in the final report)
ID
Check
Result (PASS / FAIL / UNVERIFIED)
Proof file
A1 to A6
Engine health


B1 to B6
Scheduler


C1 to C5
Traces and prompt rewrite


D1-a to D1-d
Conversation memory


D2-a to D2-e
Document memory


E1 to E5
Skills and MCP


F1 to F5
Clone deleted, attribution kept


Tests
Full suite before and after (counts from tool output)


13. Final report format
Write docs/reports/session-M/REPORT.md and also print it as your last message. Structure:
1.  Summary in at most 10 lines, with honest status per workstream.
2.  The filled verification matrix.
3.  What you changed: list of files added and modified per workstream (from git diff --stat main...session-m).
4.  What you took from OpenJarvis: exactly which ideas and which code, matching THIRD_PARTY_NOTICES.md.
5.  Deviations from this prompt and why.
6.  Things that are UNVERIFIED or FAILED, each with the reason and the exact command the owner can run to check.
7.  Known risks and what you would do next.
8.  A 'Check it yourself' section: ten commands the owner can run, with the output he should expect.
Do not claim completion of anything not in the matrix as PASS with a proof file.
14. Definition of done and stop conditions
Done means: branch session-m exists with one commit per task; baseline test failures did not increase; every acceptance check is PASS, or UNVERIFIED or FAILED with a reason; the OpenJarvis clone is gone and attribution remains; NoX starts and nx doctor runs with OpenJarvis absent.
Stop and ask the owner (write the question into docs/reports/session-M/questions.md and finish the report) if: the OpenJarvis license is not Apache-2.0; the audit shows NoX is not Python; a change would require deleting or rewriting the owner's existing data; a step needs a secret you do not have; or two workstreams conflict and you cannot choose a reversible option.
Never do these, whatever the circumstances: commit secrets; modify the owner's notes, certificates or study files; run unreviewed community skills or scripts; install OpenJarvis; report a result you did not observe.
