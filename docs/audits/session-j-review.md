# Session J Integrity Review & Audit

This audit evaluates all Session J commits, changes, and architectural claims pursuant to Session K Phase 0 specifications.

---

## 1. Itemized Review & Verdicts

### Item 1: Migration 009 Edit After Commit
- **Evidence**:
  - Migration 009 was created and committed in `df567af` (`feat(briefing): Phase 2 authoritative BriefingScheduler, migration 009, and status API`).
  - Later in `d0ca641` (`feat(cloud): Phase 4 NVIDIA cloud on by default with persistent toggle and taint protection`), `src/noxai/migrations/009_briefing_automation_and_opportunities.sql` was modified after having already been applied to existing databases, inserting:
    ```sql
    INSERT OR IGNORE INTO settings (key, value) VALUES ('cloud.enabled', 'true');
    ```
- **Analysis**:
  - SQLite migrations must be strictly immutable once committed or applied. Because migration 009 had already run on existing user systems, the appended `INSERT` never executed on systems with migration 009 marked in `schema_migrations`.
- **Verdict**: **FIX**.
  - Revert `009_briefing_automation_and_opportunities.sql` to its original commit state (`df567af`).
  - Introduce migration `010_converge_schema_and_gates.sql` ensuring that databases that applied either version of 009 converge to the identical state.

---

### Item 2: "Self-Healing Reconciliation" in `briefing/scheduler.py`
- **Evidence**:
  - In commit `29917e9`, `scheduler.py` added:
    ```python
    if active["task_status"] in ("done", "failed", "cancelled", "interrupted"):
        new_state = "succeeded" if active["task_status"] == "done" else "failed"
        await execute(conn, "UPDATE briefing_runs SET state = ? WHERE id = ?", (new_state, active["id"]))
    ```
- **Analysis**:
  - When the webserver or daemon process died or when Playwright test 10 completed, the supervisor marked the background task `done`, but the briefing pipeline had produced **0 items**. Marking a run with 0 items `succeeded` directly violates Session K Rule 4 ("A briefing run with 0 items (or below the minimum quality gate) is a FAILURE, never 'succeeded'").
- **Verdict**: **REVERT & FIX**.
  - Remove code that auto-marks runs `succeeded` based purely on task status.
  - A briefing run may ONLY be marked `succeeded` if `items_total >= briefing.min_items_total` (default 15) AND at least one news item and at least one opportunity item exist.
  - Interrupted runs whose supervisor task vanished or died must be marked `interrupted` or `failed`, never `succeeded`.

---

### Item 3: `programs.example.toml` Hardcoded Seeded Facts
- **Evidence**:
  - `programs.example.toml` included:
    ```toml
    benefits_accommodation = "provided"
    benefits_stipend = "yes"
    benefits_travel = "covered"
    typical_window = "November to January"
    ```
- **Analysis**:
  - These values were seeded as hardcoded truth rather than requiring verified live extraction. If a program changes policy or dates, the static seed creates false intelligence.
- **Verdict**: **FIX**.
  - Strip all hardcoded benefits and deadlines. Keep only `name`, `url`, `category`, `notes`, and optional `hint_window`.
  - All benefits (`housing`, `stipend`, `travel`) must strictly require verbatim snippet evidence from the fetched page text.

---

### Item 4: Eligibility Matching & User Memory Claim
- **Evidence**:
  - Earlier session reports suggested eligibility matching drew from the user's conversation memory or profile store.
- **Analysis**:
  - Reading eligibility from memory risks prompt injection and privacy egress violations (sending private facts to cloud models).
- **Verdict**: **FIX**.
  - Enforce in code that eligibility matching reads exclusively from config `[briefing.eligibility_profile]` (`nationality = "India"`, `degree_level = "undergraduate"`, `field = "computer science"`).
  - Memory system is never queried for eligibility matching.

---

### Item 5: Hand-Run UPDATE Statements on Real DB
- **Evidence**:
  - Ad-hoc scripts ran `UPDATE briefing_runs SET state='failed' WHERE state='running'` directly against the live database.
- **Analysis**:
  - Violates Rule 5: "never edit rows of the real DB by hand (no ad-hoc UPDATE/DELETE via python or sqlite3)."
- **Verdict**: **REVERT practice & FIX**.
  - Add authoritative `nx briefing repair` CLI command to find stranded runs, mark them `interrupted`, and re-queue them via `TaskSupervisor` without manual SQL edits.

---

### Item 6: Edits to `ui/e2e/ui.spec.ts` & `tests/fixtures/populate.py`
- **Evidence**:
  - `populate.py` added a parent record in `briefing_runs` (`id='jobrun-1'`) to satisfy foreign key constraints.
  - `ui/e2e/ui.spec.ts` added test 10 (single Run briefing button) and test 11 (opportunity cards and badges).
- **Analysis**:
  - The foreign key fix in `populate.py` was essential for relational integrity.
  - The Playwright tests must be tested against real instances rather than only fake fixtures.
- **Verdict**: **KEEP & HARDEN**.
  - Maintain tests, and add live temp-instance proofs for the real system.

---

### Item 7: Production Code Edits Satisfying Tests
- **Evidence**:
  - In Session J, routes and page formatting were adjusted for lint and badge text consistency.
- **Analysis**:
  - No artificial test-only bypasses or mocks were injected into production execution code.
- **Verdict**: **KEEP**. Maintain production integrity.
