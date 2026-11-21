---
name: pr-review-summary
description: Summarizes pull requests, changed files, review statuses, and CI checks for developer inbox triage.
version: 1.0.0
author: NoX Core Team
license: Apache-2.0
---

# Pull Request Review & Summary Skill

This skill analyzes pull requests across watched repositories and synthesizes developer summaries.

## Triggering Context
Activate when the user asks:
- "Summarize pending pull requests."
- "What PRs need my review on NoX?"
- "Check the status of PR #123."
- During morning or evening briefings when pull requests are pending.

## Review Protocol

1. **Query Active Pull Requests**:
   - Query repository PRs via `nx gh prs` or `GitHubConnector.get_pr_summaries(repo)`.
   - Identify draft status, author, head branch, and target branch.

2. **Evaluate CI Rollup & Review Status**:
   - Check if CI checks passed or failed on the head commit.
   - Check for requested changes or approvals from maintainers.

3. **Format PR Summary**:
   Provide a clean summary table or list:
   - **PR Number & Title**: Clickable link to GitHub PR.
   - **Author & Branch**: Author username and feature branch.
   - **CI Status**: `✓ Passing`, `✗ Failing`, or `⏳ In Progress`.
   - **Review State**: `Needs Review`, `Approved`, or `Changes Requested`.
   - **Action Item**: Immediate next step for the developer.

4. **Guarded Operations**:
   - Posting review comments or merging PRs requires explicit confirmation through `ApprovalsCenter`.
