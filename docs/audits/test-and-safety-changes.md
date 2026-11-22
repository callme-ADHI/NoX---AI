# Session O Test and Safety Changes Audit

Per Rule 6 of Session O, every change to an existing test file, or to auth, privacy, taint, egress, router, orchestrator, or config-default code, must be documented here with the rationale and assurance that no tests were weakened or deleted.

---

## 1. Modified Existing Test Files

### `tests/test_repo_hygiene.py`
- **Change**: Added `test_no_git_credential_fill_in_source_or_scripts` (N-1) and `test_honest_connector_and_channel_states_when_unconfigured` (N-2).
- **Reason**: Regression prevention for credential extraction hard ban (N-1) and enforcement of honest operational states (N-2). No existing tests were deleted or weakened.

### `tests/test_skills.py`
- **Change**: Added `test_multilanguage_skill_security_scanner_malicious_fixtures` (N-5).
- **Reason**: Validates multi-language static scanning for Python AST, Shell scripts (`curl | sh`, `rm -rf`), JS/TS (`child_process`, `exec`), and Markdown prompt injections. No existing tests were deleted or weakened.

### `tests/test_security_foundation.py`
- **Change**: Added `TestGuardedActionsAuditCoverage` testing that connector sync, credentials save, approvals lifecycle, kill switch panic, skill install, and cloud egress append tamper-evident audit events (N-3).
- **Reason**: Enforces complete audit logging coverage across guarded actions. No existing tests were deleted or weakened.

### `tests/test_cloud_health.py`
- **Change**: Set `cfg.models.fallback_policy = "auto_notify"`, relaxed exact string to match `"Cloud model is down"` or `"Cloud provider unavailable"`.
- **Reason**: Prevents headless integration test execution from blocking on interactive prompts, accommodating typed reason notifications.

### `tests/test_doctor.py`
- **Change**: Replaced hardcoded migration count limit (20) with dynamic count of files in `src/noxai/migrations/`.
- **Reason**: Allows test to remain valid as new migrations are added to the codebase.

### `tests/test_conversation_memory_repro.py`
- **Change**: Added reproduction test cases for continuous memory multi-turn context retention.
- **Reason**: Verifies bugfixes and memory persistence across turns.

### `tests/test_keypool_phase3.py`
- **Change**: Added tests for `AllocationMode` and lease tracking.
- **Reason**: Tests Phase 3 unified keypool allocation.

### `tests/test_fast_path_orchestrator.py`
- **Change**: Added unique conversation IDs, allowed typed fallback reason (`NO_KEY`/`auth`), set `fallback_policy="auto_notify"`.
- **Reason**: Documented in Session M audit; ensures conversation isolation and headless test execution.

### `tests/test_m10_integration.py`
- **Change**: Added `fallback_policy="auto_notify"` to test settings.
- **Reason**: Documented in Session M audit; prevents headless execution blocking.

### `tests/test_router.py`
- **Change**: Added `fallback_policy` parameter in `make_test_router()`.
- **Reason**: Documented in Session M audit; verifies router fallback policy behaviors.

### `tests/test_gate_a_scheduler_authority.py`
- **Change**: Updated invocation to use `_execute_schedule` instead of removed duplicate runner methods `_run_morning_briefing` / `_run_evening_briefing`, and separated SQLite context managers to prevent transaction lock contention.
- **Reason**: Fulfills C-1 (unified scheduler authority, no duplicate briefing runner in PersistentScheduler). No assertions weakened; invariant of exactly one morning run and one evening run preserved.

### `tests/test_gate_c_note_taint.py`
- **Change**: Added `test_c8_retrieval_notes_tagged_local_only_in_db_and_chunkmatch` verifying notes chunks from Obsidian Vault and Study are tagged `local_only` in DB, `ChunkMatch`, and `search_documents`.
- **Reason**: Fulfills C-8. Strengthens taint protection guarantees; no existing tests weakened or deleted.

### `tests/test_cloud_taint_recording.py`
- **Change**: Added comprehensive multi-source local_only taint isolation test verifying mail, notes, chat in private mode, and private-repo logs never reach cloud request body via RecordingTransport.
- **Reason**: Fulfills C-5. Strengthens privacy assertions; no existing tests weakened or deleted.

### `tests/test_migration_009_convergence.py`
- **Change**: Added test suite verifying pre-edit and post-edit upgrade paths of migration 009, plus upgrade convergence on real database copy.
- **Reason**: Fulfills C-7. Verifies schema and settings convergence across both historical migration paths.

### `tests/test_c2_automation_proof.py`
- **Change**: Added live automation proof test verifying morning 04:00, evening 18:00, catch-up, missed window, 0-items failure gate, and orphaned run repair under same run id.
- **Reason**: Fulfills C-2. Live proof on isolated temp instance with UI closed and no manual clicks.

### `tests/test_c3_sigkill_resume.py`
- **Change**: Added subprocess daemon SIGKILL resume test proving daemon termination and reboot repair under the same run_id.
- **Reason**: Fulfills C-3. Verifies true crash recovery with subprocess SIGKILL.






---

## 2. Safety, Auth, Egress, and Connector Files

### `src/noxai/connectors/base.py`, `gmail.py`, `imap.py`, `calendar.py`, `feeds.py`, `github.py`
- **Change**: Added `ConnectorStatus` enum. Changed `health()` to check credentials and configuration; unconfigured connectors report `NOT_CONFIGURED` instead of `HEALTHY` or `ok`. Wired `log_audit_event` on `mark_sync()` and credential saves.
- **Reason**: Enforces rule 4 (no misleading status) and rule N-3 (audit log completeness).

### `src/noxai/channels/telegram.py`, `slack.py`, `discord.py`
- **Change**: Changed `health()` to report `NOT_CONFIGURED` when tokens/webhooks are missing. Added `log_audit_event` on message dispatch.
- **Reason**: Enforces rule 4 (no misleading status) and rule N-3 (audit log completeness).

### `src/noxai/skills/security.py`
- **Change**: Replaced Python-AST only scanner with multi-language scanner covering Python, Shell, JS/TS, and Markdown prompt injection. Added `log_audit_event` on `install_untrusted_skill`.
- **Reason**: Fulfills N-5 requirement and N-3 audit logging.

### `src/noxai/models/nvidia.py`
- **Change**: Wired `log_audit_event` on cloud API egress request in `_request_with_retry`.
- **Reason**: Fulfills N-3 audit logging on cloud egress.

### `src/noxai/api/routes.py`
- **Change**: Added `log_audit_event` on data deletion routes (`disconnect_connector_endpoint`, `delete_mail_endpoint`, `remove_github_repo_endpoint`). Updated `list_connectors_endpoint` to only report `healthy: true` when status is `HEALTHY` or `ok`.
- **Reason**: Fulfills N-2 (honest API reporting) and N-3 (audit log on data deletion).

### `src/noxai/tools/builtin/web.py`
- **Change**: Removed test mode synthetic search snippets (`NOX_TEST_MODE`) from production `DuckDuckGoSearch.search()`.
- **Reason**: Fulfills T-1 requirement to keep production web search clean of test mocks.

### `tests/test_t1_live_briefing.py`
- **Change**: Created new comprehensive verification test suite for T-1 (link policy, verbatim <=25-word evidence snippets, CTF cap <= 3, drop reasons, live model proof).
- **Reason**: Proves T-1 defect resolution without regressions.

### `ui/e2e/briefing_single_button.spec.ts`
- **Change**: Fixed ESM `__dirname` ReferenceError by adopting standard `fileURLToPath(import.meta.url)`. Verified live execution generating all 4 screenshots.
- **Reason**: Proves T-2 single button reload/navigation/close survival cleanly.
