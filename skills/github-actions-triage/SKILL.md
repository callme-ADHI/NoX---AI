---
name: github-actions-triage
description: Analyzes GitHub Actions workflow failures, extracts root causes from failure logs, and recommends fixes.
version: 1.0.0
author: NoX Core Team
license: Apache-2.0
---

# GitHub Actions Failure Triage Skill

This skill analyzes failing GitHub Actions runs and extracts clear, actionable diagnostics for the developer.

## Triggering Context
Activate when the user asks:
- "Why did the GitHub Actions run fail?"
- "Triage the latest workflow failure."
- "What broke in CI?"
- When a push or PR triggers a failed Actions conclusion.

## Diagnostic Protocol

1. **Identify the Failing Run**:
   - Query recent runs: `nx gh runs` or `GitHubConnector.get_workflow_runs(repo)`.
   - Locate the failing run ID and failing workflow name.

2. **Extract Log Diagnostics**:
   - Execute failure log extraction: `nx gh triage <run_id>`.
   - Isolate the failing step header (e.g. `Run pytest`, `Build desktop`, `Lint & Typecheck`).
   - Extract the terminal error frame (assertion error, traceback, compiler error code).

3. **Classify Root Cause**:
   - `test_failure`: An automated test failed assertion or raised unexpected exception.
   - `lint_or_type_error`: Static analysis error (ruff, mypy, eslint).
   - `dependency_error`: Missing package, unmet lockfile constraint, or broken import.
   - `build_failure`: Compilation error (Cargo/Rust, Webpack/Vite).
   - `auth_error`: Missing GitHub token permission or invalid secret.
   - `timeout`: Step exceeded runner time limit.

4. **Structured Report Format**:
   Always report in this concise format:
   ```markdown
   ### ❌ GitHub Actions Triage Report — Run #<run_id> (<repo>)
   - **Workflow**: `<workflow_name>` (Branch: `<head_branch>`, Commit: `<head_sha>`)
   - **Failing Step**: `<failing_step>`
   - **Root Cause Category**: `<category>`
   - **Error Excerpt**:
     ```text
     <failing_line_1>
     <failing_line_2>
     ```
   - **Remediation**:
     `<actionable_step_to_fix_locally>`
   ```

5. **Guarded Action**:
   - If the failure was intermittent or caused by an external network flake, propose re-running the failed jobs via:
     `nx gh rerun <run_id>`
   - Always verify and request human approval before triggering remote re-runs.
