# NoX — Session E Progress

Started: 2026-10-18  
Branch: `fix/verify-phase1`  
Baseline: 228 tests, ruff clean, mypy clean, **0 git tags**, overall coverage **unknown** (claimed 97% was per 3 modules only).

Environment:
- OS: Linux (Arch) 7.1.8-arch1-3 x86_64
- Compositor: Hyprland (confirmed `$XDG_CURRENT_DESKTOP=Hyprland`, `~/.config/hypr` exists)
- Python: 3.14.7, Node: 26.7.0, npm: 12.0.2
- Ollama: 0.34.0, models: qwen2.5:14b, nomic-embed-text, gemma4:e4b, whiterabbitneo, dolphin-mistral
- Browser: Firefox (/usr/bin/firefox), no Chromium
- notify-send: /usr/bin/notify-send, hyprctl: /usr/bin/hyprctl

---

## Phases

- [ ] **E0** — Orientation and safety net  
  Status: in-progress  
  NEXT: finish E0 commit then E1

- [ ] **E1** — Trust audit (secrets, telemetry, egress, repo state)
- [ ] **E2** — Realistic-data harness
- [ ] **E3** — Fix API defects from sweep
- [ ] **E4** — Daemon correctness: lifecycle, auth, CORS, routes, logging
- [ ] **E5** — Drive real UI in real browser (Playwright)
- [ ] **E6** — Missing CLI and doctor features
- [ ] **E7** — Security gaps and coverage floors
- [ ] **E8** — Live automation proof
- [ ] **E9** — Autostart that actually works
- [ ] **E10** — Documentation made truthful
- [ ] **E11** — Final gate and merge

---

## Known Defects (from prompt)

| ID | Finding | Status |
|----|---------|--------|
| D1 | noxd.service has wrong %h/nox path | [ ] |
| D2 | PydanticSerializationError on BLOB embedding columns | [ ] |
| D3 | vector_backend log emitted per request, not once at startup | [ ] |
| D4 | CORSMiddleware in stack (spec: same-origin only) | [ ] |
| D5 | fastapi/telemetry/_asgi.py in stack — privacy concern | [ ] |
| D6 | Bare 500 + raw traceback (need {error:{code,message}} envelope) | [ ] |
| D7 | nx doctor --json missing; nx models group missing | [ ] |
| D8 | Coverage 72% overall, not 97% | [ ] |
| D9 | git tag empty — v0.1.0 tag was never created | [ ] |
| D10 | Many items absent: forget_fact safety, memory-poisoning, property test, etc. | [ ] |
| D11 | Route prefix /v1 vs spec /api; auth semantics (single-use vs lifetime token) | [ ] |
| D12 | ~/.config/hypr/hyprland.conf confirmed exists (hypr dir present) | [ ] |
| D13 | PROGRESS.md had false claims | [ ] |
| D14 | Secret-pattern scan matched extract.py, test_memory.py, test_safety.py | [ ] |
