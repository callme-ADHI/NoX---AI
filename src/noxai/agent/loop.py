"""Agent execution loop implementing tool-calling, robustness, events, and limits."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from noxai.agent.context import (
    approximate_messages_tokens,
    assemble_system_prompt,
    trim_context,
)
from noxai.agent.json_repair import repair_json
from noxai.config import Settings
from noxai.events import EventBus, global_event_bus, make_event
from noxai.memory.recall import recall_memories
from noxai.models.base import Message, ToolCall, ToolSpec
from noxai.models.router import ModelRouter
from noxai.taint import Content, TaintFlag
from noxai.tools.registry import ToolContext, ToolRegistry
from noxai.tools.registry import registry as default_registry

log = logging.getLogger(__name__)

# Max consecutive identical calls before loop detection aborts path
MAX_CONSECUTIVE_IDENTICAL_TOOL_CALLS = 3
MAX_TOOL_RESULT_CHARS = 6000


@dataclass
class AgentRunResult:
    """Outcome of an agent loop execution."""

    run_id: str
    status: str  # "done" | "failed" | "cancelled"
    text: str
    steps: int
    tool_calls_count: int
    duration_seconds: float
    citations: list[str] = field(default_factory=list)
    error: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Robustness Utilities
# ---------------------------------------------------------------------------


def detect_text_tool_call(
    text: str,
    valid_tool_names: set[str],
) -> ToolCall | None:
    """Detect if the model output plain JSON text representing a tool call."""
    clean = text.strip()
    if not clean:
        return None

    # Check for json code blocks or raw object
    candidate_str = clean
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean, re.DOTALL)
    if fence_match:
        candidate_str = fence_match.group(1).strip()
    elif not (candidate_str.startswith("{") and candidate_str.endswith("}")):
        # Look for first { to last }
        start = candidate_str.find("{")
        end = candidate_str.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate_str = candidate_str[start : end + 1]
        else:
            return None

    try:
        data = repair_json(candidate_str)
    except Exception:
        return None

    # Supported formats:
    # 1. {"name": "...", "arguments": {...}}
    # 2. {"tool": "...", "parameters": {...}}
    # 3. {"tool": "..."} or {"<tool_name>": {...}}
    tool_name = None
    args: dict[str, Any] = {}

    if "name" in data and str(data["name"]) in valid_tool_names:
        tool_name = str(data["name"])
        args = data.get("arguments", {})
    elif "tool" in data and str(data["tool"]) in valid_tool_names:
        tool_name = str(data["tool"])
        args = data.get("parameters", data.get("args", data.get("arguments", {})))
    else:
        # Check if single key is a valid tool name
        for k in data:
            if k in valid_tool_names and isinstance(data[k], dict):
                tool_name = k
                args = data[k]
                break

    if not tool_name:
        return None

    if not isinstance(args, dict):
        args = {"value": args}

    call_id = f"fallback_{uuid.uuid4().hex[:8]}"
    return ToolCall(id=call_id, name=tool_name, arguments=args)


def extract_citations(text: str) -> list[str]:
    """Extract URL or markdown reference links from final answer text."""
    urls = re.findall(r"https?://[^\s)\]\"'>]+", text)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for u in urls:
        clean = u.rstrip(".,;:)")
        if clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


# ---------------------------------------------------------------------------
# Agent Loop
# ---------------------------------------------------------------------------


CHAT_LANE_TOOLS = {
    "web_search",
    "fetch_page",
    "reminder_create",
    "reminder_cancel",
    "reminder_snooze",
    "job_list",
    "job_get",
    "job_create",
    "job_update",
    "system_info",
    "get_time",
    "notify",
    "recall_facts",
    "save_fact",
    "forget_fact",
    "pin_fact",
    "preference_get",
    "preference_set",
    "run_shell",
}
FRONT_DESK_CLOUD_TOOLS = {"web_search", "fetch_page"}


class AgentLoop:
    """Executes multi-step tool-calling agent runs with full safety and event reporting."""

    def __init__(
        self,
        config: Settings,
        router: ModelRouter,
        registry: ToolRegistry | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.registry = registry or default_registry
        self.event_bus = event_bus or global_event_bus

    async def run(
        self,
        user_message: str | Message,
        *,
        run_id: str | None = None,
        conversation_id: str | None = None,
        allow_cloud: bool = False,
        lane: str = "chat",
        tools: list[ToolSpec] | None = None,
        max_steps: int = 20,
        timeout_seconds: float = 120.0,
        max_tokens: int | None = None,
        db: aiosqlite.Connection | None = None,
        request_approval: (
            Callable[[str, str, dict[str, Any], str, str | None], Coroutine[Any, Any, bool]] | None
        ) = None,
        on_token: Callable[[str], None] | None = None,
    ) -> AgentRunResult:
        """Execute a complete agent run until a final text response is produced."""
        start_time = time.perf_counter()
        actual_run_id = run_id or uuid.uuid4().hex

        if tools is not None:
            valid_tools = list(tools)
        elif lane == "chat":
            if allow_cloud:
                filtered = [
                    t for t in self.registry.get_specs() if t.name in FRONT_DESK_CLOUD_TOOLS
                ]
            else:
                filtered = [t for t in self.registry.get_specs() if t.name in CHAT_LANE_TOOLS]
            valid_tools = filtered if filtered else self.registry.get_specs()
        else:
            valid_tools = self.registry.get_specs()
        valid_tool_names = {t.name for t in valid_tools}

        # 1. Publish run_started event
        await self.event_bus.publish(
            make_event(
                "run_started",
                {
                    "run_id": actual_run_id,
                    "conversation_id": conversation_id,
                    "allow_cloud": allow_cloud,
                },
                run_id=actual_run_id,
            ),
            db=db,
        )

        # 2. Build initial message list
        messages: list[Message] = []

        # Trace accumulators per F1.1
        t_route_total = 0.0
        t_memory_recall_total = 0.0
        t_prompt_build_total = 0.0
        t_provider_connect_total = 0.0
        t_ttft_total = 0.0
        t_generation_total = 0.0
        tool_time_by_name: dict[str, float] = {}
        t_post_extraction_total = 0.0
        model_used_name = ""

        # Recall memories if local and DB available
        t_mem_0 = time.perf_counter()
        recalled_mem_list: list[str] | None = None
        if not allow_cloud and db is not None:
            user_text_raw = (
                user_message if isinstance(user_message, str) else user_message.get_text()
            )
            try:
                rec_res = await recall_memories(
                    query=user_text_raw,
                    db=db,
                    router=self.router,
                    config=self.config,
                )
                if rec_res.formatted_block:
                    recalled_mem_list = [rec_res.formatted_block]
            except Exception as exc:
                log.warning("Memory recall failed: %s", exc)
        t_memory_recall_total = (time.perf_counter() - t_mem_0) * 1000

        # System prompt assembly with stable_prefix for KV caching
        t_prompt_0 = time.perf_counter()
        system_text = await assemble_system_prompt(
            config=self.config,
            tools=valid_tools,
            db=db,
            allow_cloud=allow_cloud,
            recalled_memories=recalled_mem_list,
            stable_prefix=True,
        )
        system_message = Message(
            role="system",
            content=Content(
                text=system_text,
                taint=TaintFlag.PRIVATE if recalled_mem_list else TaintFlag.NONE,
            ),
        )
        messages.append(system_message)
        t_prompt_build_total = (time.perf_counter() - t_prompt_0) * 1000

        # Load existing conversation messages from DB if conversation_id provided (local runs only)
        if not allow_cloud and conversation_id and db is not None:
            try:
                async with db.execute(
                    """
                    SELECT role, content, tool_call_id, tool_name, taint_flags
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                ) as cursor:
                    async for row in cursor:
                        role, content_text, tc_id, t_name, t_flags = row
                        msg_content = Content(
                            text=str(content_text),
                            taint=TaintFlag(int(t_flags or 0)),
                        )
                        messages.append(
                            Message(
                                role=str(role),
                                content=msg_content,
                                tool_call_id=tc_id,
                                name=t_name,
                            )
                        )
            except Exception as exc:
                log.warning("Failed to load conversation %s from db: %s", conversation_id, exc)

        # Append incoming user message
        if isinstance(user_message, str):
            user_msg = Message(role="user", content=Content.public(user_message))
        else:
            user_msg = user_message
        messages.append(user_msg)

        # Persist user message in DB if connected
        if conversation_id and db is not None:
            try:
                taint_val = (
                    user_msg.content.taint.value if isinstance(user_msg.content, Content) else 0
                )
                await db.execute(
                    """
                    INSERT INTO messages (id, conversation_id, role, content, taint_flags)
                    VALUES (?, ?, 'user', ?, ?)
                    """,
                    (uuid.uuid4().hex, conversation_id, user_msg.get_text(), taint_val),
                )
                await db.commit()
            except Exception as exc:
                log.warning("Could not persist user message: %s", exc)

        if db is not None:
            try:
                await db.execute(
                    """
                    INSERT INTO runs (id, kind, conversation_id, status, allow_cloud, started_at)
                    VALUES (?, 'chat', ?, 'running', ?, datetime('now'))
                    ON CONFLICT(id) DO UPDATE SET status = 'running'
                    """,
                    (actual_run_id, conversation_id, 1 if allow_cloud else 0),
                )
                await db.commit()
            except Exception as exc:
                log.warning("Could not persist run state: %s", exc)

        # Tool execution loop state
        current_context_taint = TaintFlag.NONE
        for msg in messages:
            if isinstance(msg.content, Content):
                current_context_taint |= msg.content.taint

        step_count = 0
        total_tool_calls = 0
        final_answer = ""
        loop_history: list[tuple[str, str]] = []  # Track (tool_name, serialized_args)
        json_repair_retries: dict[str, int] = {}  # Track retries per tool call id

        background_tasks: set[asyncio.Task[Any]] = set()

        def _handle_notice(n_text: str) -> None:
            t = asyncio.create_task(
                self.event_bus.publish(make_event("notice", {"text": n_text}, run_id=actual_run_id))
            )
            background_tasks.add(t)
            t.add_done_callback(background_tasks.discard)

        def _handle_model_used(m_info: dict[str, Any]) -> None:
            nonlocal model_used_name
            model_used_name = str(m_info.get("model", model_used_name))
            t = asyncio.create_task(
                self.event_bus.publish(make_event("model_used", m_info, run_id=actual_run_id))
            )
            background_tasks.add(t)
            t.add_done_callback(background_tasks.discard)

        def _handle_tool_emit(evt: dict[str, Any]) -> None:
            t = asyncio.create_task(
                self.event_bus.publish(
                    make_event(evt.get("type", "tool_event"), evt, run_id=actual_run_id),
                    db=db,
                )
            )
            background_tasks.add(t)
            t.add_done_callback(background_tasks.discard)

        def _make_trace() -> dict[str, Any]:
            total_elapsed_ms = (time.perf_counter() - start_time) * 1000
            prompt_tokens_est = sum(len(m.get_text()) for m in messages) // 4
            return {
                "route": round(t_route_total, 2),
                "memory_recall": round(t_memory_recall_total, 2),
                "prompt_build": round(t_prompt_build_total, 2),
                "provider_connect": round(t_provider_connect_total, 2),
                "time_to_first_token": round(t_ttft_total, 2),
                "generation": round(t_generation_total, 2),
                "tool_time": {k: round(v, 2) for k, v in tool_time_by_name.items()},
                "post_run_extraction": round(t_post_extraction_total, 2),
                "total": round(total_elapsed_ms, 2),
                "prompt_tokens_est": prompt_tokens_est,
                "model_used": model_used_name or "brain",
            }

        async def _persist_trace(t_data: dict[str, Any]) -> None:
            await self.event_bus.publish(
                make_event("trace", t_data, run_id=actual_run_id),
                db=db,
            )
            if db is not None:
                try:
                    await db.execute(
                        "UPDATE runs SET trace_json = ? WHERE id = ?",
                        (json.dumps(t_data), actual_run_id),
                    )
                    await db.commit()
                except Exception as exc:
                    log.warning("Could not persist trace_json: %s", exc)

        try:
            while step_count < max_steps:
                step_count += 1

                # Check timeout
                elapsed = time.perf_counter() - start_time
                if elapsed > timeout_seconds:
                    timeout_err = f"Run exceeded timeout of {timeout_seconds} seconds."
                    await self.event_bus.publish(
                        make_event("run_failed", {"error": timeout_err}, run_id=actual_run_id),
                        db=db,
                    )
                    t_data = _make_trace()
                    await _persist_trace(t_data)
                    return AgentRunResult(
                        run_id=actual_run_id,
                        status="failed",
                        text="I apologize, but this request timed out before completing.",
                        steps=step_count,
                        tool_calls_count=total_tool_calls,
                        duration_seconds=elapsed,
                        error=timeout_err,
                        trace=t_data,
                    )

                # Context trimming and dynamic num_ctx
                num_ctx = getattr(self.config.models.ollama, "num_ctx", 8192)
                trimmed_messages = await trim_context(
                    messages=messages,
                    max_tokens=num_ctx,
                    router=self.router,
                )
                est_tokens = approximate_messages_tokens(trimmed_messages)
                dynamic_ctx = min(num_ctx, max(2048, est_tokens + (max_tokens or 1024) + 256))

                t_route_0 = time.perf_counter()
                role = "heavy" if (allow_cloud and self.router.is_cloud_available()) else "brain"
                t_route_total += (time.perf_counter() - t_route_0) * 1000

                assistant_chunks: list[str] = []
                tool_calls: list[ToolCall] = []

                t_stream_0 = time.perf_counter()
                first_token_time: float | None = None
                async for chunk in self.router.chat(
                    role=role,
                    messages=trimmed_messages,
                    tools=valid_tools,
                    num_ctx=dynamic_ctx,
                    max_tokens=max_tokens,
                    on_notice=_handle_notice,
                    on_model_used=_handle_model_used,
                ):
                    if chunk.text:
                        if first_token_time is None:
                            first_token_time = time.perf_counter()
                            ttft = (first_token_time - t_stream_0) * 1000
                            t_ttft_total += ttft
                            t_provider_connect_total += ttft
                        assistant_chunks.append(chunk.text)
                        if on_token:
                            on_token(chunk.text)
                        await self.event_bus.publish(
                            make_event("token", {"chunk": chunk.text}, run_id=actual_run_id)
                        )
                    if chunk.tool_calls:
                        tool_calls.extend(chunk.tool_calls)

                if first_token_time is not None:
                    t_generation_total += (time.perf_counter() - first_token_time) * 1000
                else:
                    t_generation_total += (time.perf_counter() - t_stream_0) * 1000

                assistant_text = "".join(assistant_chunks).strip()

                # Text-only tool-call fallback: if no tool calls came via structured channel
                if not tool_calls and assistant_text:
                    fallback_call = detect_text_tool_call(assistant_text, valid_tool_names)
                    if fallback_call:
                        log.info("Detected text-only tool-call fallback: %s", fallback_call.name)
                        tool_calls.append(fallback_call)

                # If no tool calls were requested, the run is complete!
                if not tool_calls:
                    final_answer = assistant_text
                    assistant_msg = Message(
                        role="assistant",
                        content=Content.public(final_answer),
                    )
                    messages.append(assistant_msg)

                    if conversation_id and db is not None:
                        try:
                            await db.execute(
                                """
                                INSERT INTO messages
                                (id, conversation_id, role, content, taint_flags)
                                VALUES (?, ?, 'assistant', ?, 0)
                                """,
                                (uuid.uuid4().hex, conversation_id, final_answer),
                            )
                            await db.commit()
                        except Exception as exc:
                            log.warning("Could not persist assistant message: %s", exc)

                    await self.event_bus.publish(
                        make_event(
                            "message_done",
                            {"role": "assistant", "content": final_answer},
                            run_id=actual_run_id,
                        ),
                        db=db,
                    )
                    await self.event_bus.publish(
                        make_event(
                            "run_done",
                            {"status": "done", "steps": step_count},
                            run_id=actual_run_id,
                        ),
                        db=db,
                    )

                    # Trigger background memory extraction (local model only)
                    if not allow_cloud and db is not None:
                        try:
                            from noxai.memory.extract import extract_and_store_turn

                            user_str = (
                                user_message
                                if isinstance(user_message, str)
                                else user_message.get_text()
                            )
                            is_untrusted = bool(current_context_taint & TaintFlag.UNTRUSTED)
                            t = asyncio.create_task(
                                extract_and_store_turn(
                                    user_text=user_str,
                                    assistant_text=final_answer,
                                    router=self.router,
                                    db=None,
                                    task_id=actual_run_id,
                                    is_untrusted=is_untrusted,
                                )
                            )
                            background_tasks.add(t)
                            t.add_done_callback(background_tasks.discard)
                        except Exception as exc:
                            log.warning("Could not schedule memory extraction: %s", exc)

                    citations = extract_citations(final_answer)
                    t_data = _make_trace()
                    await _persist_trace(t_data)
                    return AgentRunResult(
                        run_id=actual_run_id,
                        status="done",
                        text=final_answer,
                        steps=step_count,
                        tool_calls_count=total_tool_calls,
                        duration_seconds=time.perf_counter() - start_time,
                        citations=citations,
                        trace=t_data,
                    )

                # Process returned tool calls
                messages.append(
                    Message(
                        role="assistant",
                        content=assistant_text,
                        tool_calls=tool_calls,
                    )
                )

                for call in tool_calls:
                    total_tool_calls += 1
                    call_name = call.name
                    raw_args = call.arguments

                    # Parse / repair arguments if raw string was passed
                    parsed_args: dict[str, Any]
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = repair_json(raw_args)
                        except Exception as exc:
                            retries = json_repair_retries.get(call.id, 0)
                            if retries < 2:
                                json_repair_retries[call.id] = retries + 1
                                err_msg = (
                                    f"Your arguments were invalid JSON for tool '{call_name}': "
                                    f"{exc}. Please retry with valid JSON arguments."
                                )
                                messages.append(
                                    Message(
                                        role="tool",
                                        content=err_msg,
                                        tool_call_id=call.id,
                                        name=call_name,
                                    )
                                )
                                continue
                            else:
                                parsed_args = {}
                    elif isinstance(raw_args, dict):
                        parsed_args = raw_args
                    else:
                        parsed_args = {"value": raw_args}

                    # Check unknown tool
                    if call_name not in valid_tool_names:
                        err_msg = (
                            f"Unknown tool '{call_name}'. Valid tools: {sorted(valid_tool_names)}"
                        )
                        messages.append(
                            Message(
                                role="tool",
                                content=err_msg,
                                tool_call_id=call.id,
                                name=call_name,
                            )
                        )
                        continue

                    # Loop detection: check consecutive identical calls
                    serialized_args = json.dumps(parsed_args, sort_keys=True)
                    loop_history.append((call_name, serialized_args))
                    if len(loop_history) >= MAX_CONSECUTIVE_IDENTICAL_TOOL_CALLS:
                        last_3 = loop_history[-MAX_CONSECUTIVE_IDENTICAL_TOOL_CALLS:]
                        if all(entry == last_3[0] for entry in last_3):
                            loop_msg = (
                                f"Tool '{call_name}' was called "
                                f"{MAX_CONSECUTIVE_IDENTICAL_TOOL_CALLS} times in a row "
                                "with identical arguments. Try a different approach or finish."
                            )
                            log.warning("Loop detection triggered on %s", call_name)
                            messages.append(
                                Message(
                                    role="tool",
                                    content=loop_msg,
                                    tool_call_id=call.id,
                                    name=call_name,
                                )
                            )
                            continue

                    # Determine tier for tool event
                    tool_def = self.registry.get(call_name)
                    tier = tool_def.tier if tool_def else "read"

                    # Emit tool_call event
                    await self.event_bus.publish(
                        make_event(
                            "tool_call",
                            {
                                "tool": call_name,
                                "args": parsed_args,
                                "tier": tier,
                            },
                            run_id=actual_run_id,
                        ),
                        db=db,
                    )

                    # Build ToolContext
                    tool_ctx = ToolContext(
                        run_id=actual_run_id,
                        config=self.config,
                        allow_cloud=allow_cloud,
                        db=db,
                        context_taint=current_context_taint,
                        request_approval=request_approval,
                        router=self.router,
                        emit=_handle_tool_emit,
                    )

                    # Execute tool via registry
                    exec_start = time.perf_counter()
                    tool_result = await self.registry.execute(
                        call_name,
                        parsed_args,
                        tool_ctx,
                    )
                    exec_duration_ms = (time.perf_counter() - exec_start) * 1000
                    tool_time_by_name[call_name] = (
                        tool_time_by_name.get(call_name, 0.0) + exec_duration_ms
                    )

                    # Output truncation (6,000 chars)
                    raw_output = tool_result.output
                    if len(raw_output) > MAX_TOOL_RESULT_CHARS:
                        truncated_output = raw_output[:MAX_TOOL_RESULT_CHARS] + "\n[truncated]"
                    else:
                        truncated_output = raw_output

                    # Taint escalation
                    is_untrusted = bool(tool_def and tool_def.untrusted_output and tool_result.ok)
                    if is_untrusted:
                        current_context_taint |= TaintFlag.UNTRUSTED

                    # Emit tool_result event
                    await self.event_bus.publish(
                        make_event(
                            "tool_result",
                            {
                                "tool": call_name,
                                "ok": tool_result.ok,
                                "summary": tool_result.summary,
                                "duration_ms": exec_duration_ms,
                            },
                            run_id=actual_run_id,
                        ),
                        db=db,
                    )

                    # Append tool result message
                    tool_content = (
                        Content.untrusted(truncated_output, source=call_name)
                        if is_untrusted
                        else Content.public(truncated_output)
                    )
                    messages.append(
                        Message(
                            role="tool",
                            content=tool_content,
                            tool_call_id=call.id,
                            name=call_name,
                        )
                    )

                    # Persist tool message in DB if connected
                    if conversation_id and db is not None:
                        try:
                            taint_val = TaintFlag.UNTRUSTED.value if is_untrusted else 0
                            await db.execute(
                                """
                                INSERT INTO messages
                                (id, conversation_id, role, content,
                                 tool_call_id, tool_name, taint_flags)
                                VALUES (?, ?, 'tool', ?, ?, ?, ?)
                                """,
                                (
                                    uuid.uuid4().hex,
                                    conversation_id,
                                    truncated_output,
                                    call.id,
                                    call_name,
                                    taint_val,
                                ),
                            )
                            await db.commit()
                        except Exception as exc:
                            log.warning("Could not persist tool message: %s", exc)

            # Reached max steps
            max_step_msg = f"Reached maximum allowed steps ({max_steps}) for this run."
            await self.event_bus.publish(
                make_event("run_failed", {"error": max_step_msg}, run_id=actual_run_id),
                db=db,
            )
            t_data = _make_trace()
            await _persist_trace(t_data)
            return AgentRunResult(
                run_id=actual_run_id,
                status="failed",
                text="I reached the maximum step limit while working on your request.",
                steps=step_count,
                tool_calls_count=total_tool_calls,
                duration_seconds=time.perf_counter() - start_time,
                error=max_step_msg,
                trace=t_data,
            )

        except asyncio.CancelledError:
            log.info("Agent run %s was cancelled.", actual_run_id)
            await self.event_bus.publish(
                make_event("run_cancelled", {"status": "cancelled"}, run_id=actual_run_id),
                db=db,
            )
            t_data = _make_trace()
            await _persist_trace(t_data)
            return AgentRunResult(
                run_id=actual_run_id,
                status="cancelled",
                text="Run cancelled by user.",
                steps=step_count,
                tool_calls_count=total_tool_calls,
                duration_seconds=time.perf_counter() - start_time,
                error="Cancelled",
                trace=t_data,
            )
        except Exception as exc:
            log.error("Unhandled error in agent run %s: %s", actual_run_id, exc, exc_info=True)
            await self.event_bus.publish(
                make_event("run_failed", {"error": str(exc)}, run_id=actual_run_id),
                db=db,
            )
            t_data = _make_trace()
            await _persist_trace(t_data)
            return AgentRunResult(
                run_id=actual_run_id,
                status="failed",
                text=f"An error occurred while executing the request: {exc}",
                steps=step_count,
                tool_calls_count=total_tool_calls,
                duration_seconds=time.perf_counter() - start_time,
                error=str(exc),
                trace=t_data,
            )
