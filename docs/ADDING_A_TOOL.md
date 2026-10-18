# Adding a Tool to NoX

Tools are async Python functions decorated with `@tool(...)` that the agent
can call. This guide covers everything you need to ship a new tool in Phase 1.

---

## 1. Where to put it

- **Built-in tools** (shipped with NoX): `src/noxai/tools/builtin/<category>.py`
- **User-added tools** (hot-loaded, no restart needed): `~/.local/share/nox/tools/<name>.py`

User tools are loaded at startup and watched for changes. They are forced to
at least `write` tier until marked trusted in the UI.

---

## 2. Anatomy of a tool

```python
from __future__ import annotations
from pydantic import BaseModel, Field
from noxai.tools.registry import tool, ToolResult, ToolContext

# 1. Define a strict Pydantic params model
class MyToolParams(BaseModel):
    model_config = {"extra": "forbid"}   # reject unexpected args

    query: str = Field(..., description="What to look for.", min_length=1)
    max_results: int = Field(default=5, ge=1, le=20)

# 2. Register the function
@tool(
    name="my_tool",
    description="One clear sentence — this is what the model sees.",
    params=MyToolParams,
    tier="read",          # "read" | "write" | "dangerous" | "forbidden"
    data_class="public",  # "public" | "private"
    untrusted_output=False,  # True → wrap result in <untrusted_content> tags
    timeout=15,           # seconds; 0 = use agent.tool_timeout_seconds from config
)
async def my_tool(params: MyToolParams, ctx: ToolContext) -> ToolResult:
    """Docstring is for developers; `description` above is for the model."""
    result_text = f"Found: {params.query}"
    return ToolResult(
        ok=True,
        output=result_text,
        summary=f"Searched for '{params.query}', got 1 result",
    )
```

---

## 3. Tiers

| Tier | Behaviour |
|---|---|
| `read` | Runs automatically — no approval needed. |
| `write` | Pauses the run and shows an approval card in the UI. Owner clicks Approve/Deny. Can be added to the always-allow whitelist by a UI click (not by chat). |
| `dangerous` | Also requires the owner to type `confirm` in the approval card. |
| `forbidden` | Never runs. No approval path. |

**Taint escalation:** if the run context contains `UNTRUSTED` content (web,
RSS, fetched files), every `write` and `dangerous` call requires approval
even if whitelisted. The approval card shows a warning.

---

## 4. ToolContext

`ctx` is passed to your function by the runner. It provides:

```python
ctx.run_id          # str — current run ID
ctx.allow_cloud     # bool — is this a cloud-eligible run?
ctx.config          # Settings — full config object
ctx.db              # aiosqlite.Connection — use sparingly; prefer helpers
ctx.emit(event)     # send a WebSocket event (e.g. node_progress)
ctx.cancel_event    # asyncio.Event — check .is_set() in long loops
```

---

## 5. ToolResult

```python
@dataclass
class ToolResult:
    ok: bool          # True = success
    output: str       # Full text returned to the model (capped at 6,000 chars)
    summary: str      # Short (≤ 80 chars) for UI display and audit log
    sources: list[dict] = field(default_factory=list)  # [{"title":…, "url":…}]
```

If your tool returns untrusted data, set `untrusted_output=True` in the
decorator — the registry will wrap the output in `<untrusted_content>` tags
automatically. Do not wrap it yourself.

---

## 6. Error handling

- **Never raise** inside a tool. Catch exceptions and return
  `ToolResult(ok=False, output=f"Error: {exc}", summary="Tool failed")`.
- The runner catches any uncaught exception and converts it to a tool error
  result so the run continues.

---

## 7. Audit logging

Every tool call is automatically written to `audit_log` by the runner. You do
not need to do this yourself. Do not log secrets or sensitive data inside
your tool — the audit logger redacts common patterns, but defense-in-depth
means you should not log them at all.

---

## 8. Testing your tool

Write a test using `FakeProvider` and `FakeToolContext`:

```python
async def test_my_tool() -> None:
    from noxai.tools.builtin.my_module import my_tool, MyToolParams
    from tests.fakes import FakeToolContext

    ctx = FakeToolContext()
    result = await my_tool(MyToolParams(query="test"), ctx)

    assert result.ok
    assert "test" in result.output
```

---

## 9. Checklist before submitting

- [ ] Params model has `extra="forbid"` and docstrings on all fields
- [ ] Tier is set conservatively (prefer `write` over `read` if in doubt)
- [ ] `untrusted_output` is True if the output comes from the network or files
- [ ] No `shell=True` subprocess calls
- [ ] File paths validated against `allowed_file_roots` and `denied_paths`
- [ ] Timeout is set appropriately
- [ ] Tests written and passing
- [ ] Tool added to the module that `registry.py` auto-discovers
