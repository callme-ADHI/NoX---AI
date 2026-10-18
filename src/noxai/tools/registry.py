"""Tool decorator, schema generation, discovery, execution, and audit logging."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from noxai.config import Settings
from noxai.models.base import ToolSpec
from noxai.taint import Content, TaintFlag
from noxai.tools.safety import Tier, redact_secrets

log = logging.getLogger(__name__)

USER_TOOLS_DIR = Path("~/.local/share/nox/tools").expanduser()


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass
class ToolContext:
    """Execution context provided to tool functions."""

    run_id: str
    config: Settings
    allow_cloud: bool = False
    db: Any | None = None
    emit: Callable[[dict[str, Any]], None] | None = None
    cancel_event: asyncio.Event | None = None
    context_taint: TaintFlag = TaintFlag.NONE
    # Approval callback hook for tests/API
    request_approval: (
        Callable[[str, str, dict[str, Any], Tier, str | None], Coroutine[Any, Any, bool]] | None
    ) = None


@dataclass
class ToolResult:
    """Standardized output returned by every tool."""

    ok: bool
    output: str
    summary: str
    sources: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ToolDefinition:
    """Internal registration metadata for a tool."""

    name: str
    description: str
    params_model: type[BaseModel]
    tier: Tier
    func: Callable[..., Coroutine[Any, Any, ToolResult]]
    data_class: str = "public"
    untrusted_output: bool = False
    timeout: float = 30.0
    is_user_tool: bool = False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Central registry for discovering, validating, and executing tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool_def: ToolDefinition | Any) -> None:
        """Register a tool definition or decorated tool function."""
        actual_def: ToolDefinition
        if hasattr(tool_def, "tool_def"):
            actual_def = tool_def.tool_def
        elif isinstance(tool_def, ToolDefinition):
            actual_def = tool_def
        else:
            raise TypeError(f"Expected ToolDefinition or decorated function, got {type(tool_def)}")
        self._tools[actual_def.name] = actual_def
        log.debug("Registered tool: %s (tier=%s)", actual_def.name, actual_def.tier)

    def get(self, name: str) -> ToolDefinition | None:
        """Retrieve tool definition by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        """Return all registered tools."""
        return list(self._tools.values())

    def get_tool_specs(self) -> list[ToolSpec]:
        """Generate ToolSpec JSON schemas suitable for LLM tool calling."""
        specs: list[ToolSpec] = []
        for t in self._tools.values():
            schema = t.params_model.model_json_schema()
            # Clean up schema for model consumption
            schema.pop("title", None)
            specs.append(
                ToolSpec(
                    name=t.name,
                    description=t.description,
                    parameters=schema,
                )
            )
        return specs

    def get_specs(self) -> list[ToolSpec]:
        """Alias for get_tool_specs()."""
        return self.get_tool_specs()

    def auto_discover(self) -> None:
        """Auto-discover built-in tools and custom user tools."""
        builtin_modules = [
            "noxai.tools.builtin.system",
            "noxai.tools.builtin.file_tools",
            "noxai.tools.builtin.web",
        ]
        for mod_name in builtin_modules:
            try:
                importlib.import_module(mod_name)
            except Exception as exc:
                log.warning("Could not load tool module %s: %s", mod_name, exc)

        # Discover custom user tools in ~/.local/share/nox/tools/*.py
        if USER_TOOLS_DIR.exists():
            for script in USER_TOOLS_DIR.glob("*.py"):
                if script.name.startswith("__"):
                    continue
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"user_tool_{script.stem}", script
                    )
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        log.info("Loaded custom user tool from %s", script)
                except Exception as exc:
                    log.error("Failed to load user tool %s: %s", script, exc)

    async def execute(
        self,
        name: str,
        raw_args: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        """Execute tool with argument validation, security checks, approvals, and audit log."""
        start_time = time.perf_counter()
        tool_def = self.get(name)

        if not tool_def:
            valid_names = sorted(self._tools.keys())
            return ToolResult(
                ok=False,
                output=f"Unknown tool '{name}'. Valid tools: {valid_names}",
                summary=f"Unknown tool: {name}",
            )

        # Validate arguments against Pydantic model
        try:
            params = tool_def.params_model(**raw_args)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            await self._audit_log(
                ctx=ctx,
                tool=name,
                args_json=redact_secrets(json.dumps(raw_args)),
                tier=tool_def.tier,
                decision="blocked",
                approver="system",
                result_summary=f"Invalid arguments: {exc}",
                duration_ms=duration_ms,
                ok=False,
            )
            return ToolResult(
                ok=False,
                output=f"Invalid arguments for tool '{name}': {exc}",
                summary="Invalid tool arguments",
            )

        # Check permission tier
        tier = tool_def.tier
        if tool_def.is_user_tool and tier == "read":
            tier = "write"  # user tools forced to at least write tier unless trusted

        if tier == "forbidden":
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            await self._audit_log(
                ctx=ctx,
                tool=name,
                args_json=redact_secrets(json.dumps(raw_args)),
                tier=tier,
                decision="blocked",
                approver="system",
                result_summary="Forbidden tool execution attempted.",
                duration_ms=duration_ms,
                ok=False,
            )
            return ToolResult(
                ok=False,
                output=f"Tool '{name}' is forbidden and cannot be executed.",
                summary="Tool is forbidden",
            )

        # Taint escalation: untrusted context forces approval on write and dangerous tools
        has_untrusted_context = bool(ctx.context_taint & TaintFlag.UNTRUSTED)
        needs_approval = tier in ("write", "dangerous")

        decision = "auto"
        approver = "system"

        if needs_approval:
            approval_reason = None
            if has_untrusted_context:
                approval_reason = "This run has read untrusted web/external content."

            if ctx.request_approval:
                approved = await ctx.request_approval(
                    ctx.run_id,
                    name,
                    raw_args,
                    tier,
                    approval_reason,
                )
            else:
                approved = False  # default deny if no approval handler configured

            if not approved:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                await self._audit_log(
                    ctx=ctx,
                    tool=name,
                    args_json=redact_secrets(json.dumps(raw_args)),
                    tier=tier,
                    decision="denied",
                    approver="owner",
                    result_summary="Action was denied by owner or timed out.",
                    duration_ms=duration_ms,
                    ok=False,
                )
                return ToolResult(
                    ok=False,
                    output=f"Action '{name}' was denied by owner or timed out.",
                    summary="Action denied",
                )
            decision = "approved"
            approver = "owner"

        # Execute the tool
        try:
            timeout = tool_def.timeout or ctx.config.agent.tool_timeout_seconds
            result = await asyncio.wait_for(tool_def.func(params, ctx), timeout=timeout)
        except TimeoutError:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            await self._audit_log(
                ctx=ctx,
                tool=name,
                args_json=redact_secrets(json.dumps(raw_args)),
                tier=tier,
                decision="timeout",
                approver=approver,
                result_summary=f"Timed out after {tool_def.timeout}s",
                duration_ms=duration_ms,
                ok=False,
            )
            return ToolResult(
                ok=False,
                output=f"Tool '{name}' timed out after {tool_def.timeout}s.",
                summary="Tool execution timed out",
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            await self._audit_log(
                ctx=ctx,
                tool=name,
                args_json=redact_secrets(json.dumps(raw_args)),
                tier=tier,
                decision=decision,
                approver=approver,
                result_summary=f"Error: {exc}",
                duration_ms=duration_ms,
                ok=False,
            )
            return ToolResult(
                ok=False,
                output=f"Error executing tool '{name}': {exc}",
                summary="Tool failed with error",
            )

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        # Output capping: keep at most 6,000 characters
        capped_output = result.output
        if len(capped_output) > 6000:
            capped_output = capped_output[:6000] + "\n[truncated]"

        # Untrusted wrapping if applicable
        if tool_def.untrusted_output and result.ok:
            wrapped = Content.untrusted(capped_output).as_model_text()
            final_output = wrapped
            ctx.context_taint |= TaintFlag.UNTRUSTED
        else:
            final_output = capped_output

        await self._audit_log(
            ctx=ctx,
            tool=name,
            args_json=redact_secrets(json.dumps(raw_args)),
            tier=tier,
            decision=decision,
            approver=approver,
            result_summary=result.summary[:200],
            duration_ms=duration_ms,
            ok=result.ok,
        )

        return ToolResult(
            ok=result.ok,
            output=final_output,
            summary=result.summary,
            sources=result.sources,
        )

    async def _audit_log(
        self,
        ctx: ToolContext,
        tool: str,
        args_json: str,
        tier: str,
        decision: str,
        approver: str,
        result_summary: str,
        duration_ms: int,
        ok: bool,
    ) -> None:
        """Write an execution record to the audit_log table."""
        if not ctx.db:
            return
        try:
            await ctx.db.execute(
                """
                INSERT INTO audit_log (
                    run_id, tool, args_json, tier, decision, approver,
                    result_summary, duration_ms, ok
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx.run_id,
                    tool,
                    args_json,
                    tier,
                    decision,
                    approver,
                    result_summary,
                    duration_ms,
                    1 if ok else 0,
                ),
            )
            await ctx.db.commit()
        except Exception as exc:
            log.error("Failed to write to audit_log: %s", exc)


# Global registry singleton
registry = ToolRegistry()
default_registry = registry


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def tool(
    name: str,
    description: str,
    params: type[BaseModel],
    tier: Tier = "read",
    data_class: str = "public",
    untrusted_output: bool = False,
    timeout: float = 30.0,
) -> Callable[..., Any]:
    """Decorator to declare and register a tool."""

    def decorator(func: Callable[..., Coroutine[Any, Any, ToolResult]]) -> Callable[..., Any]:
        tool_def = ToolDefinition(
            name=name,
            description=description,
            params_model=params,
            tier=tier,
            func=func,
            data_class=data_class,
            untrusted_output=untrusted_output,
            timeout=timeout,
        )
        func.tool_def = tool_def  # type: ignore[attr-defined]
        registry.register(tool_def)
        return func

    return decorator
