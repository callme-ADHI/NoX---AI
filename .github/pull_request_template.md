## Description
<!-- Provide a clear description of the problem solved, architectural changes, and new features introduced. -->

## Changes Checklist
- [ ] Conforms to NoX architecture decisions in `docs/DECISIONS.md`.
- [ ] Database migrations tested on fresh and copied databases with automatic backup.
- [ ] All sensitive inputs, emails, chats, and notes remain `local_only` by default.
- [ ] Guardrails active (InputScanner, OutputScanner, PIIRedactor).
- [ ] Zero real secrets or keys tracked in git history.
- [ ] Unit tests added and all suites green (`uv run pytest`, `npm test`).
- [ ] UI build passes with zero errors (`npm run build`).
- [ ] Diagnostics pass (`nx health`, `nx selftest`, `nx doctor`).

## Verification & Test Results
<!-- Paste test run summaries and CLI health diagnostics output. -->
