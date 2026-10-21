"""Unified Chat Orchestrator for NoX (§1.4, §G3).

Exposes ``orchestrate_chat_turn(...)`` as the single canonical entry point
for all chat turns (FastAPI route ``POST /api/chat``, WebSocket, and ``nx chat``).
Enforces:
1. Reflex router intent analysis (<50ms zero-LLM).
2. Explicit content labeling at creation (CHAT, PUBLIC, LOCAL_ONLY, SECRET).
3. Egress filtering and zero-leakage privacy rules.
4. CloudHealth check and dynamic fallback (NO_KEY, INVALID_KEY, RATE_LIMITED,
   DEGRADED, OFFLINE, SENSITIVE, PRIVATE_MODE, PRIVACY_BLOCKED:<reason>, LOCAL_LANE).
5. Immediate unbuffered token streaming via on_token and event_bus.
6. Local fallback to resident model (or brain for multi-step agent tasks).
7. Slim cloud front desk prompt (<1500 tokens, stable prefix, front-desk tools only,
   empty profile card, never LOCAL_ONLY content).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from noxai.config import Settings, get_config
from noxai.db import execute
from noxai.events import EventBus, global_event_bus, make_event
from noxai.models.base import Message, ToolCall, ToolSpec
from noxai.models.cloud_health import CloudHealthState, get_cloud_health
from noxai.models.router import ModelRouter, get_default_router
from noxai.router.intent import IntentResult, analyze_intent
from noxai.security.egress_filter import record_egress_decision
from noxai.security.sensitivity import is_sensitive_prompt, mask_secrets
from noxai.taint import Content, DataClass, TaintFlag
from noxai.tools.registry import ToolRegistry, default_registry

log = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()

FRONT_DESK_ALLOWED_TOOLS = {
    "web_search",
    "fetch_page",
    "get_time",
    "system_info",
    "list_files",
    "read_file",
    "run_shell",
    "job_list",
    "job_get",
    "reminder_create",
    "reminder_list",
    "reminder_get",
    "reminder_cancel",
    "reminder_snooze",
    "recall_facts",
    "save_fact",
    "dispatch_task",
    "dispatch_reminder",
    "dispatch_job_edit",
    "request_approval",
}


def _parse_text_tool_calls(text: str) -> list[ToolCall]:
    """Parse XML-style <tool_call> and bare <function=...> tags from model text if present."""
    import re

    tool_calls: list[ToolCall] = []

    # First, search for explicit <tool_call> blocks
    blocks = re.findall(r"<tool_call>([\s\S]*?)</tool_call>", text)
    if not blocks:
        # Also match bare <function=...>...</function> blocks if model omitted <tool_call>
        blocks = re.findall(r"(<function=[a-zA-Z0-9_\-]+>[\s\S]*?</function>)", text)

    for block in blocks:
        fn_match = re.search(r"<function=([a-zA-Z0-9_\-]+)>([\s\S]*?)</function>", block)
        if fn_match:
            fn_name = fn_match.group(1).strip()
            param_body = fn_match.group(2)
            params: dict[str, Any] = {}
            for p in re.finditer(
                r"<parameter=([a-zA-Z0-9_\-]+)>([\s\S]*?)</parameter>", param_body
            ):
                p_key = p.group(1).strip()
                p_val = p.group(2).strip()
                if p_val.isdigit():
                    params[p_key] = int(p_val)
                elif p_val.lower() == "true":
                    params[p_key] = True
                elif p_val.lower() == "false":
                    params[p_key] = False
                else:
                    if (p_val.startswith('"') and p_val.endswith('"')) or (
                        p_val.startswith("'") and p_val.endswith("'")
                    ):
                        p_val = p_val[1:-1].strip()
                    params[p_key] = p_val
            tool_calls.append(
                ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=fn_name,
                    arguments=params,
                )
            )
    return tool_calls


def _strip_text_tool_calls(text: str) -> str:
    """Remove <tool_call>, <function>, and <parameter> blocks from assistant text."""
    import re

    cleaned = re.sub(r"<tool_call>[\s\S]*?(?:</tool_call>|$)", "", text)
    cleaned = re.sub(r"<function=[a-zA-Z0-9_\-]+>[\s\S]*?(?:</function>|$)", "", cleaned)
    cleaned = re.sub(r"<parameter=[a-zA-Z0-9_\-]+>[\s\S]*?(?:</parameter>|$)", "", cleaned)
    cleaned = re.sub(r"</?(?:tool_call|function|parameter)[^>]*>", "", cleaned)
    return cleaned.strip()


@dataclass
class ChatTurnResult:
    """Outcome of an orchestrated chat turn."""

    run_id: str
    conversation_id: str
    text: str
    model_name: str
    provider_name: str
    is_cloud: bool
    fallback_reason: str | None
    intent: IntentResult
    trace: dict[str, Any]
    citations: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


def _build_cloud_system_prompt() -> Message:
    """Construct a slim, privacy-safe system prompt for the cloud front desk (§G3.7).

    Guarantees:
    - Stable prefix for KV cache reuse.
    - Front-desk and system tools only.
    - Zero LOCAL_ONLY data (profile card remains empty by default).
    - Labelled DataClass.PUBLIC.
    """
    prompt_text = (
        "You are NoX, an advanced, highly capable autonomous personal AI assistant created "
        "to serve the owner on their Linux system.\n"
        "You have complete internal self-awareness:\n"
        "- Architecture: Fast hybrid engine powered by a multi-key NVIDIA NIM cloud pool "
        "(Nemotron-3.5-Lightning-30B-A3B) with local-first SQLite persistence, "
        "autonomous JobScheduler, and security egress filters.\n"
        "- Reminders & Scheduling: When the user asks to be reminded or schedule an alert, "
        "you MUST invoke reminder_create (or dispatch_reminder). NEVER hallucinate a confirmation "
        "or invent a fake reminder ID in text without calling the tool.\n"
        "- Reminder Inquiries: When the user asks about a reminder, asks what reminders are active, "
        "or mentions a reminder ID (e.g. rem_...), you MUST invoke reminder_get or reminder_list. "
        "Never invoke list_files for reminders or reminder IDs.\n"
        "- Self-Inspection Tools: Use job_list and job_get to inspect scheduled jobs, "
        "cron triggers, and background tasks. Use reminder_list and reminder_get for reminders. "
        "Use recall_facts to recall stored user memories and facts.\n"
        "- Live System & Web Tools: Use get_time for current date and time. Use system_info "
        "and run_shell for system telemetry and commands. Use list_files and read_file "
        "to explore the system. Use web_search and fetch_page for live internet queries.\n"
        "- Background Tasks: Use dispatch_task and dispatch_job_edit to schedule background jobs.\n"
        "- Tool Execution Rules: Always formulate your answers from real tool outputs. "
        "Never repeat the same tool call with identical arguments in a single turn. "
        "Never output raw <tool_call> or <function=...> XML tags in your conversational text.\n"
        "Provide direct, structured, and insightful answers."
    )
    return Message(
        role="system",
        content=Content(text=prompt_text, data_class=DataClass.PUBLIC, taint=TaintFlag.NONE),
    )


def _build_local_system_prompt(recalled_memories: list[str] | None = None) -> Message:
    """Construct the system prompt for local execution."""
    prompt_text = (
        "You are NoX, an advanced, highly capable personal AI assistant running locally.\n"
        "You have complete internal self-awareness:\n"
        "- Architecture: Hybrid autonomous system with local execution and SQLite persistence.\n"
        "- Reminders & Inspection: Use reminder_create to set reminders, and reminder_list / "
        "reminder_get to inspect reminders. Never hallucinate fake reminder confirmations.\n"
        "- Self-Inspection: Use job_list, job_get, reminder_list, and recall_facts to inspect "
        "jobs and memories.\n"
        "- System & Tools: Use get_time, system_info, list_files, read_file, run_shell, "
        "web_search, fetch_page.\n"
        "Provide direct, structured, and helpful answers."
    )
    if recalled_memories:
        prompt_text += "\n\nRecalled Context:\n" + "\n".join(recalled_memories)
    return Message(
        role="system",
        content=Content(
            text=prompt_text,
            data_class=DataClass.LOCAL_ONLY if recalled_memories else DataClass.PUBLIC,
            taint=TaintFlag.PRIVATE if recalled_memories else TaintFlag.NONE,
        ),
    )


async def orchestrate_chat_turn(
    message: str,
    *,
    conversation_id: str | None = None,
    run_id: str | None = None,
    allow_cloud: bool = True,
    is_private_mode: bool = False,
    db: aiosqlite.Connection | None = None,
    router: ModelRouter | None = None,
    config: Settings | None = None,
    event_bus: EventBus | None = None,
    registry: ToolRegistry | None = None,
    tools: list[ToolSpec] | None = None,
    on_token: Callable[[str], None] | None = None,
    on_notice: Callable[[str], None] | None = None,
    on_model_used: Callable[[dict[str, Any]], None] | None = None,
) -> ChatTurnResult:
    """Orchestrate a single chat turn through the reflex router and appropriate model engine.

    All chat entry points (REST, WebSocket, CLI) MUST route through this function.
    """
    start_time = time.perf_counter()
    actual_run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
    actual_conv_id = conversation_id or f"conv_{uuid.uuid4().hex[:12]}"

    rtr = router or get_default_router(config)
    cfg = config or (getattr(rtr, "config", None) or get_config())
    bus = event_bus or global_event_bus
    reg = registry or default_registry

    db_ctx: Any | None = None
    if db is None:
        from noxai.db import get_db

        db_ctx = get_db()
        active_db = await db_ctx.__aenter__()
    else:
        active_db = db

    # 1. Reflex Router Intent Analysis (<50ms zero-LLM) (§1.4, §G3.1)
    t_route_0 = time.perf_counter()
    intent_res = analyze_intent(message)
    # Mask secrets first so secret tokens are redacted rather than misclassified as sensitive topics
    masked_message = mask_secrets(message)
    is_sens, _sens_reason = is_sensitive_prompt(masked_message)
    has_sensitive_topic = is_sens or any(
        topic in intent_res.reason
        for topic in ("health_medical", "financial_banking", "auth_credentials")
    )
    t_route = (time.perf_counter() - t_route_0) * 1000.0

    # 2. Content labeling at creation (§G3.2)
    # The owner's message is CHAT (or LOCAL_ONLY if Private mode is active)
    user_data_class = DataClass.LOCAL_ONLY if is_private_mode else DataClass.CHAT
    user_taint = TaintFlag.PRIVATE if is_private_mode else TaintFlag.NONE
    user_content = Content(text=message, data_class=user_data_class, taint=user_taint)
    user_msg = Message(role="user", content=user_content)

    # Publish lifecycle start events
    await bus.publish(
        make_event(
            "run_started",
            {
                "run_id": actual_run_id,
                "conversation_id": actual_conv_id,
                "message": message,
                "allow_cloud": allow_cloud,
                "is_private_mode": is_private_mode,
            },
            run_id=actual_run_id,
        ),
        db=active_db,
    )
    await bus.publish(
        make_event("intent_detected", intent_res.to_dict(), run_id=actual_run_id),
        db=active_db,
    )

    async def _safe_db_write(write_fn: Callable[[Any], Any]) -> None:
        try:
            await write_fn(active_db)
            await active_db.commit()
        except Exception as exc:
            log.warning("Database write failed: %s", exc)

    async def _write_user_msg(conn: Any) -> None:
        await execute(
            conn,
            """
            INSERT INTO conversations (id, title, created_at, updated_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                          strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(id) DO UPDATE SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            (actual_conv_id, message[:40]),
        )
        await execute(
            conn,
            """
            INSERT INTO messages (id, conversation_id, role, content, taint_flags)
            VALUES (?, ?, 'user', ?, ?)
            """,
            (f"msg_{uuid.uuid4().hex[:12]}", actual_conv_id, message, user_taint.value),
        )

    await _safe_db_write(_write_user_msg)

    # 3. CloudHealth check and engine determination (§G3.3, §G3.4)
    nvidia_provider = getattr(rtr, "providers", {}).get("nvidia")
    ch = getattr(nvidia_provider, "cloud_health", None) or get_cloud_health()
    if ch.secrets_path:
        ch.check_secrets_file()

    has_nvidia_key = bool(
        getattr(nvidia_provider, "api_key", None)
        or (cfg.nvidia_api_key and cfg.nvidia_api_key.get_secret_value())
        or ch._current_key
    )

    target_role = "front"
    fallback_reason: str | None = None

    if not allow_cloud:
        target_role = "resident"
        fallback_reason = "LOCAL_LANE"
    elif is_private_mode:
        target_role = "resident"
        fallback_reason = "PRIVATE_MODE"
    elif has_sensitive_topic:
        target_role = "resident"
        fallback_reason = "SENSITIVE"
    elif not has_nvidia_key or ch.state == CloudHealthState.NO_KEY:
        target_role = "resident"
        fallback_reason = "NO_KEY"
    elif ch.state == CloudHealthState.INVALID_KEY:
        target_role = "resident"
        fallback_reason = "INVALID_KEY"
    elif ch.state == CloudHealthState.RATE_LIMITED:
        target_role = "resident"
        fallback_reason = "RATE_LIMITED"
    elif ch.state == CloudHealthState.OFFLINE:
        target_role = "resident"
        fallback_reason = "OFFLINE"
    elif ch.state == CloudHealthState.DEGRADED:
        if not ch.circuit_breaker.can_attempt():
            target_role = "resident"
            fallback_reason = "DEGRADED"
        else:
            target_role = "front"
    elif not ch.is_active():
        target_role = "resident"
        fallback_reason = "DEGRADED"

    # 4. Prompt Assembly & Egress Filter Evaluation (§G3.2, §G3.7)
    t_prompt_0 = time.perf_counter()
    all_specs = tools if tools is not None else reg.get_specs()
    front_desk_specs = [s for s in all_specs if s.name in FRONT_DESK_ALLOWED_TOOLS]

    if target_role == "front":
        # Slim cloud front-desk prompt (<1500 tokens target)
        sys_msg = _build_cloud_system_prompt()
        selected_tools = front_desk_specs

        cloud_turn_messages = [sys_msg, user_msg]

        # Evaluate Egress Filter (§1.2, §G3.2)
        try:
            cloud_provider, cloud_model_name = rtr.resolve_role("front")
        except Exception:
            cloud_provider, cloud_model_name = None, "nvidia/nemotron"

        provider_name = cloud_provider.name if cloud_provider else "nvidia"
        egress_decision = rtr.egress_filter.evaluate(
            cloud_turn_messages,
            provider_name=provider_name,
            model=cloud_model_name,
            is_local=False,
        )

        try:
            await record_egress_decision(
                decision=egress_decision,
                provider=provider_name,
                model=cloud_model_name,
                db=active_db,
            )
        except Exception as exc:
            log.debug("record_egress_decision error: %s", exc)

        if not egress_decision.allowed:
            # Privacy policy v2 blocked cloud transmission
            target_role = "resident"
            fallback_reason = f"PRIVACY_BLOCKED:{egress_decision.reason}"
            messages_to_send = [_build_local_system_prompt(), user_msg]
            selected_tools = []
        else:
            messages_to_send = (
                egress_decision.sanitized_messages
                if egress_decision.sanitized_messages is not None
                else cloud_turn_messages
            )
    else:
        # Local execution (resident model unless multi-step tools require brain)
        sys_msg = _build_local_system_prompt()
        messages_to_send = [sys_msg, user_msg]
        selected_tools = []

    t_prompt = (time.perf_counter() - t_prompt_0) * 1000.0

    # 5. Execute Chat Generation with Immediate Streaming (§G3.5, §G3.6)
    model_used_record: dict[str, Any] = {}

    def _on_model_used_internal(info: dict[str, Any]) -> None:
        nonlocal model_used_record
        model_used_record = dict(info)
        if fallback_reason and "fallback_reason" not in model_used_record:
            model_used_record["fallback_reason"] = fallback_reason
            model_used_record["fallback_from"] = "front"
        if on_model_used:
            on_model_used(model_used_record)
        task = asyncio.create_task(
            bus.publish(
                make_event("model_used", model_used_record, run_id=actual_run_id),
                db=active_db,
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    chunks: list[str] = []
    first_token_time: float | None = None
    t_stream_0 = time.perf_counter()
    citations_collected: list[str] = []

    try:
        max_tool_steps = 3
        step = 0
        executed_tool_signatures: set[str] = set()
        last_tool_res_text: str = ""
        force_synthesis = False

        while step <= max_tool_steps:
            turn_chunks: list[str] = []
            turn_tool_calls: list[ToolCall] = []

            # If force_synthesis or reached max_tool_steps, don't pass tools to force text response
            tools_for_step = (
                selected_tools
                if (selected_tools and step < max_tool_steps and not force_synthesis)
                else None
            )

            async for chunk in rtr.chat(
                role=target_role,
                messages=messages_to_send,
                tools=tools_for_step,
                stream=True,
                on_notice=on_notice,
                on_model_used=_on_model_used_internal,
            ):
                if chunk.text:
                    if first_token_time is None:
                        first_token_time = time.perf_counter()
                    turn_chunks.append(chunk.text)
                    current_turn_so_far = "".join(turn_chunks)
                    # Suppress token streaming if it's a tool call XML tag
                    if not any(
                        tag in current_turn_so_far
                        for tag in ("<tool_call", "<function=", "<parameter=")
                    ) and not current_turn_so_far.lstrip().startswith("<"):
                        if on_token:
                            on_token(chunk.text)
                        await bus.publish(
                            make_event("token", {"chunk": chunk.text}, run_id=actual_run_id)
                        )

                if chunk.tool_calls:
                    for tc in chunk.tool_calls:
                        existing = next(
                            (
                                t
                                for t in turn_tool_calls
                                if (t.id and t.id == tc.id) or (t.name and t.name == tc.name)
                            ),
                            None,
                        )
                        if existing:
                            if tc.id:
                                existing.id = tc.id
                            if tc.name:
                                existing.name = tc.name
                            if tc.arguments and tc.arguments != {}:
                                existing.arguments = tc.arguments
                        else:
                            turn_tool_calls.append(tc)

            chunks = turn_chunks

            # If no tool calls were made in delta, check for XML-style tool calls in text
            if not turn_tool_calls:
                text_tcs = _parse_text_tool_calls("".join(turn_chunks))
                if text_tcs:
                    turn_tool_calls.extend(text_tcs)
                else:
                    break

            # Deduplication: check if any tool call was already executed in this turn
            duplicate_call = False
            for c in turn_tool_calls:
                c_sig = f"{c.name}:{json.dumps(c.arguments, sort_keys=True)}"
                if c_sig in executed_tool_signatures:
                    duplicate_call = True
                    break

            if duplicate_call:
                # Model repeated a tool call already executed; break loop to synthesize
                break

            for c in turn_tool_calls:
                c_sig = f"{c.name}:{json.dumps(c.arguments, sort_keys=True)}"
                executed_tool_signatures.add(c_sig)

            # Check if this turn executed an action tool; if so, next turn must be text synthesis
            action_tools = {
                "reminder_create",
                "reminder_snooze",
                "reminder_cancel",
                "dispatch_reminder",
                "dispatch_job_edit",
                "dispatch_task",
                "save_fact",
                "job_create",
                "job_update",
                "job_delete",
            }
            if any(c.name in action_tools for c in turn_tool_calls):
                force_synthesis = True

            step += 1

            for call in turn_tool_calls:
                tool_item = reg.get(call.name)
                tier_val = getattr(tool_item, "tier", "read") if tool_item else "read"
                await bus.publish(
                    make_event(
                        "tool_call",
                        {
                            "id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                            "tier": tier_val,
                            "tool": call.name,
                            "args": call.arguments,
                            "call_id": call.id,
                        },
                        run_id=actual_run_id,
                    ),
                    db=active_db,
                )

            # Append assistant message with tool calls to history
            assistant_text = _strip_text_tool_calls("".join(turn_chunks).strip())
            assistant_turn_msg = Message(
                role="assistant",
                content=Content.chat(assistant_text),
                tool_calls=turn_tool_calls,
            )
            messages_to_send.append(assistant_turn_msg)

            from noxai.tools.registry import ToolContext

            tool_ctx = ToolContext(
                run_id=actual_run_id,
                config=cfg,
                allow_cloud=allow_cloud,
                db=active_db,
                router=rtr,
            )

            for call in turn_tool_calls:
                t0_call = time.perf_counter()
                tool_def = reg.get(call.name)
                tool_res_text = ""
                if tool_def is not None:
                    try:
                        tool_res = await reg.execute(call.name, call.arguments, tool_ctx)
                        tool_res_text = str(
                            tool_res.output if tool_res.ok else f"Error: {tool_res.output}"
                        )
                        last_tool_res_text = tool_res_text
                        if call.name in ("web_search", "fetch_page"):
                            import re

                            urls = re.findall(r"https?://[^\s)\]\"'>]+", tool_res_text)
                            citations_collected.extend(urls)
                        t_call_dur = int((time.perf_counter() - t0_call) * 1000)
                        await bus.publish(
                            make_event(
                                "tool_result",
                                {
                                    "id": call.id,
                                    "name": call.name,
                                    "tool": call.name,
                                    "ok": tool_res.ok,
                                    "summary": tool_res.summary,
                                    "result_summary": tool_res.summary,
                                    "output": tool_res_text[:500],
                                    "duration_ms": t_call_dur,
                                },
                                run_id=actual_run_id,
                            ),
                            db=active_db,
                        )
                    except Exception as t_err:
                        log.warning("Tool %s execution failed: %s", call.name, t_err)
                        tool_res_text = f"Tool execution failed: {t_err}"
                        last_tool_res_text = tool_res_text
                else:
                    tool_res_text = f"Tool {call.name} not found in registry."
                    last_tool_res_text = tool_res_text

                is_untrusted = tool_def.untrusted_output if tool_def else False
                tool_content = (
                    Content.untrusted(tool_res_text, source=call.name)
                    if is_untrusted
                    else Content.public(tool_res_text, source=call.name)
                )

                messages_to_send.append(
                    Message(
                        role="tool",
                        content=tool_content,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

    except Exception as exc:
        log.error("Execution error during chat turn: %s", exc)
        # Handle unexpected failure with safe local fallback if not already fallback
        if target_role == "front":
            fallback_reason = "DEGRADED"
            local_role = "resident"
            local_chunks: list[str] = []
            try:
                async for chunk in rtr.chat(
                    role=local_role,
                    messages=[_build_local_system_prompt(), user_msg],
                    stream=True,
                    on_notice=on_notice,
                    on_model_used=_on_model_used_internal,
                ):
                    if chunk.text:
                        if first_token_time is None:
                            first_token_time = time.perf_counter()
                        local_chunks.append(chunk.text)
                        if on_token:
                            on_token(chunk.text)
                        await bus.publish(
                            make_event("token", {"chunk": chunk.text}, run_id=actual_run_id)
                        )
                chunks = local_chunks
            except Exception as fallback_exc:
                log.error("Fallback chat failed: %s", fallback_exc)
                chunks.append(
                    f"I encountered an error while processing your request: {fallback_exc}"
                )

    t_now = time.perf_counter()
    t_ttft = (first_token_time - t_stream_0) * 1000.0 if first_token_time else 0.0
    t_generation = (t_now - (first_token_time or t_stream_0)) * 1000.0
    total_elapsed = t_now - start_time
    total_elapsed_ms = total_elapsed * 1000.0

    final_text = _strip_text_tool_calls("".join(chunks).strip())
    if not final_text and last_tool_res_text:
        final_text = f"✅ {last_tool_res_text}"
    from noxai.agent.loop import extract_citations

    for c in extract_citations(final_text):
        if c not in citations_collected:
            citations_collected.append(c)

    # Determine effective model and provider info
    actual_model_name = str(model_used_record.get("model", "unknown"))
    actual_provider_name = str(model_used_record.get("provider", "unknown"))
    actual_is_local = bool(model_used_record.get("is_local", target_role == "resident"))
    actual_fallback_reason = model_used_record.get("fallback_reason") or fallback_reason

    prompt_tokens_est = sum(len(m.get_text()) for m in messages_to_send) // 4

    trace_data: dict[str, Any] = {
        "route": round(t_route, 2),
        "prompt_build": round(t_prompt, 2),
        "provider_connect": round(t_ttft, 2),
        "time_to_first_token": round(t_ttft, 2),
        "generation": round(t_generation, 2),
        "total": round(total_elapsed_ms, 2),
        "prompt_tokens_est": prompt_tokens_est,
        "model_used": actual_model_name,
        "provider": actual_provider_name,
        "role": model_used_record.get("role", target_role),
        "intent": intent_res.intent.value,
        "fallback_reason": actual_fallback_reason,
    }

    lane_info = "cloud" if not actual_is_local else "local_resident"

    # Persist assistant response to DB
    async def _write_assistant_msg(conn: Any) -> None:
        await execute(
            conn,
            """
            INSERT INTO messages (id, conversation_id, role, content, taint_flags)
            VALUES (?, ?, 'assistant', ?, 0)
            """,
            (f"msg_{uuid.uuid4().hex[:12]}", actual_conv_id, final_text),
        )
        await execute(
            conn,
            """
            INSERT INTO runs (
                id, kind, conversation_id, status, allow_cloud, started_at, trace_json
            )
            VALUES (?, 'chat', ?, 'done', ?, datetime('now'), ?)
            ON CONFLICT(id) DO UPDATE SET status = 'done', trace_json = ?
            """,
            (
                actual_run_id,
                actual_conv_id,
                0 if actual_is_local else 1,
                json.dumps(trace_data),
                json.dumps(trace_data),
            ),
        )

    await _safe_db_write(_write_assistant_msg)

    # Publish completion events
    await bus.publish(
        make_event("trace", trace_data, run_id=actual_run_id),
        db=active_db,
    )
    await bus.publish(
        make_event(
            "message_done",
            {
                "text": final_text,
                "lane": lane_info,
                "model_name": actual_model_name,
                "is_cloud": not actual_is_local,
                "latency_ms": int(total_elapsed_ms),
                "fallback_reason": actual_fallback_reason,
                "trace": trace_data,
            },
            run_id=actual_run_id,
        ),
        db=active_db,
    )
    await bus.publish(
        make_event("run_done", {"status": "done"}, run_id=actual_run_id),
        db=active_db,
    )

    if db_ctx is not None:
        await db_ctx.__aexit__(None, None, None)

    return ChatTurnResult(
        run_id=actual_run_id,
        conversation_id=actual_conv_id,
        text=final_text,
        model_name=actual_model_name,
        provider_name=actual_provider_name,
        is_cloud=not actual_is_local,
        fallback_reason=actual_fallback_reason,
        intent=intent_res,
        trace=trace_data,
        citations=citations_collected,
        duration_seconds=total_elapsed,
    )
