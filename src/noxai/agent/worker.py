"""Worker agent execution for executing subtask nodes with tools and structured output."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from noxai.agent.json_repair import repair_json
from noxai.models.base import ChatChunk, Message, ToolCall, ToolSpec
from noxai.taint import Content, TaintFlag
from noxai.tools.registry import ToolContext

if TYPE_CHECKING:
    import aiosqlite

    from noxai.agent.planner import SubtaskNode
    from noxai.config import Settings
    from noxai.models.router import ModelRouter
    from noxai.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

WORKER_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "worker.md"
MAX_WORKER_STEPS = 6
MAX_TOOL_RESULT_CHARS = 4000


def _load_worker_prompt() -> str:
    if WORKER_PROMPT_PATH.exists():
        return WORKER_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "You are a specialized Worker Agent. Execute the assigned objective using tools and return "
        'valid JSON matching: {"summary": str, "key_points": [str], '
        '"sources": [{"title": str, "url": str}], "confidence": float, "gaps": [str]}'
    )


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class WorkerSource(BaseModel):
    """Source cited by worker."""

    title: str = Field(..., description="Document or web page title.")
    url: str = Field(..., description="Absolute URL or resource location.")

    @model_validator(mode="before")
    @classmethod
    def _coerce_source(cls, v: Any) -> Any:
        if isinstance(v, str):
            return {"title": v, "url": v}
        if isinstance(v, dict):
            url = v.get("url") or v.get("link") or v.get("href") or ""
            title = (
                v.get("title")
                or v.get("name")
                or v.get("headline")
                or v.get("cve_headline")
                or url
                or "Source"
            )
            return {"title": str(title), "url": str(url)}
        return v


class WorkerOutput(BaseModel):
    """Structured result returned by a worker subtask."""

    summary: str = Field(..., description="Comprehensive findings summary.")
    key_points: list[str] = Field(
        default_factory=list,
        description="Key factual bullet points discovered.",
    )
    sources: list[WorkerSource] = Field(
        default_factory=list,
        description="List of cited sources.",
    )
    confidence: float = Field(
        1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0.",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Unanswered aspects or information gaps.",
    )

    @field_validator("key_points", mode="before")
    @classmethod
    def _coerce_key_points(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return [str(v)] if v else []
        res = []
        for item in v:
            if isinstance(item, str):
                res.append(item)
            elif isinstance(item, dict):
                headline = item.get("headline") or item.get("cve_headline") or item.get("title")
                url = item.get("url") or item.get("link")
                body = (
                    item.get("body")
                    or item.get("summary")
                    or item.get("description")
                    or item.get("text")
                )
                parts = []
                if headline:
                    parts.append(str(headline))
                if body:
                    parts.append(str(body))
                if not parts:
                    parts = [str(val) for k, val in item.items() if val and k != "url"]
                line = " — ".join(parts)
                if url:
                    line += f" ({url})"
                res.append(line if line else str(item))
            else:
                res.append(str(item))
        return res

    @field_validator("gaps", mode="before")
    @classmethod
    def _coerce_gaps(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return [str(v)] if v else []
        res = []
        for item in v:
            if isinstance(item, str):
                res.append(item)
            elif isinstance(item, dict):
                vals = [str(val) for k, val in item.items() if val]
                res.append(" — ".join(vals) if vals else str(item))
            else:
                res.append(str(item))
        return res


def parse_worker_json(text: str) -> WorkerOutput:
    """Parse worker response text into a validated WorkerOutput."""
    clean = text.strip()
    if "```json" in clean:
        clean = clean.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in clean:
        clean = clean.split("```", 1)[1].split("```", 1)[0].strip()

    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(clean)
    except Exception:
        try:
            parsed = repair_json(clean)
        except Exception as err:
            log.debug("Worker output repair_json failed: %s", err)

    if isinstance(parsed, dict):
        if "summary" not in parsed:
            for alt_key in ("findings", "overview", "result", "analysis", "content", "response"):
                if alt_key in parsed and isinstance(parsed[alt_key], str):
                    parsed["summary"] = parsed[alt_key]
                    break
        if "summary" in parsed:
            try:
                return WorkerOutput.model_validate(parsed)
            except Exception as exc:
                log.warning("Pydantic validation failed for worker JSON: %s", exc)

    # Heuristic fallback if model returned plain text
    return WorkerOutput(
        summary=clean[:1500] if clean else "No output produced.",
        key_points=[],
        sources=[],
        confidence=0.4,
        gaps=["Worker response was not structured JSON."],
    )


# ---------------------------------------------------------------------------
# Worker Execution Loop
# ---------------------------------------------------------------------------


async def execute_worker_subtask(
    node: SubtaskNode,
    dependency_outputs: dict[str, WorkerOutput],
    router: ModelRouter,
    registry: ToolRegistry,
    config: Settings,
    allow_cloud: bool = False,
    db: aiosqlite.Connection | None = None,
    emit_progress: Callable[[str], None] | None = None,
    run_id: str | None = None,
) -> WorkerOutput:
    """Execute a single worker node subtask with a fresh context and tool execution."""
    worker_system = _load_worker_prompt()
    role = "heavy" if allow_cloud else "brain"

    # Build prompt with dependency outputs
    prompt_parts = [
        f"Assigned Subtask: [{node.id}] {node.title}",
        f"Kind: {node.kind}",
        f"Objective: {node.objective}",
        f"Expected Output: {node.expected_output}",
    ]
    if node.suggested_tools:
        prompt_parts.append(f"Suggested Tools: {', '.join(node.suggested_tools)}")

    if dependency_outputs:
        prompt_parts.append("\nFindings from Prerequisite Tasks:")
        for dep_id, dep_out in dependency_outputs.items():
            prompt_parts.append(f"- [{dep_id}]: {dep_out.summary}")
            if dep_out.key_points:
                prompt_parts.append(f"  Key points: {'; '.join(dep_out.key_points[:3])}")

    messages: list[Message] = [
        Message(
            role="system",
            content=Content.public(worker_system) if allow_cloud else worker_system,
        ),
        Message(
            role="user",
            content=Content.public("\n".join(prompt_parts))
            if allow_cloud
            else "\n".join(prompt_parts),
        ),
    ]

    tool_specs: list[ToolSpec] = [t.to_spec() for t in registry.list_tools()]
    actual_run_id = run_id or f"worker_{node.id}"

    step_count = 0
    final_output_text = ""
    current_taint = TaintFlag.NONE
    executed_tool_calls: list[ToolCall] = []

    while step_count < MAX_WORKER_STEPS:
        step_count += 1
        chunks: list[ChatChunk] = []

        try:
            async for chunk in router.chat(
                role=role,
                messages=messages,
                tools=tool_specs if tool_specs else None,
                stream=False,
            ):
                chunks.append(chunk)
        except Exception as exc:
            log.warning("Worker LLM call failed in node %s: %s", node.id, exc)
            raise RuntimeError(f"Worker LLM call failed in node {node.id}: {exc}") from exc

        # Reconstruct response
        full_text = "".join(c.text for c in chunks if c.text)
        tool_calls: list[ToolCall] = []
        for c in chunks:
            if c.tool_calls:
                tool_calls.extend(c.tool_calls)

        # Detect tool call in text if not parsed by provider
        if not tool_calls and full_text.strip().startswith("{") and '"tool"' in full_text:
            try:
                raw_call = json.loads(full_text.strip())
                if isinstance(raw_call, dict) and "tool" in raw_call:
                    tool_calls.append(
                        ToolCall(
                            id=f"text_call_{step_count}",
                            name=str(raw_call["tool"]),
                            arguments=raw_call.get("args", {}),
                        )
                    )
            except Exception as err:
                log.debug("Failed to parse text-only tool call: %s", err)

        # If no tool calls, this is the worker's conclusion
        if not tool_calls:
            final_output_text = full_text
            break

        # Execute tool calls
        executed_tool_calls.extend(tool_calls)
        messages.append(
            Message(
                role="assistant",
                content=Content.public(full_text) if allow_cloud else full_text,
                tool_calls=tool_calls,
            )
        )

        for call in tool_calls:
            call_name = call.name
            tool_args = call.arguments or {}

            if emit_progress:
                emit_progress(f"Running tool {call_name}")

            tool_ctx = ToolContext(
                run_id=actual_run_id,
                config=config,
                allow_cloud=allow_cloud,
                db=db,
                context_taint=current_taint,
                router=router,
            )

            tool_def = registry.get(call_name)
            tool_result = await registry.execute(call_name, tool_args, tool_ctx)

            raw_out = tool_result.output
            if len(raw_out) > MAX_TOOL_RESULT_CHARS:
                raw_out = raw_out[:MAX_TOOL_RESULT_CHARS] + "\n[truncated]"

            is_untrusted = bool(tool_def and tool_def.untrusted_output and tool_result.ok)
            if is_untrusted:
                current_taint |= TaintFlag.UNTRUSTED

            tool_content = (
                Content.untrusted(raw_out, source=call_name)
                if is_untrusted
                else Content.public(raw_out)
            )

            messages.append(
                Message(
                    role="tool",
                    content=tool_content,
                    tool_call_id=call.id,
                    name=call_name,
                )
            )

    output = parse_worker_json(final_output_text)

    # Heuristic: Extract cited URLs if sources list was left empty by LLM
    if not output.sources:
        import re

        url_pattern = re.compile(r"https?://[^\s)\]\"'>]+")
        search_texts = [output.summary] + output.key_points
        for m in messages:
            if m.role == "tool" and m.content:
                search_texts.append(str(m.content))
        found_urls = url_pattern.findall(" ".join(search_texts))
        if found_urls:
            distinct_urls = list(dict.fromkeys(found_urls))[:5]
            output.sources = [WorkerSource(title=u, url=u) for u in distinct_urls]
        elif node.kind == "research" and not executed_tool_calls:
            output.confidence = min(output.confidence, 0.4)

    # Retry rule per Section 6.4:
    # "if a node's confidence is below 0.5 or it lists gaps that matter,
    # run ONE refinement attempt with a rewritten query. Never more than one."
    gaps_present = bool(output.gaps and len(output.gaps) >= 1)
    if output.confidence < 0.5 and gaps_present:
        log.info(
            "Node %s triggered 1-attempt refinement (confidence=%.2f)",
            node.id,
            output.confidence,
        )
        if emit_progress:
            emit_progress(f"Refining search for gaps in {node.id}")

        refine_msg = (
            f"Your previous attempt had confidence {output.confidence:.2f} and reported gaps:\n"
            f"- {'; '.join(output.gaps)}\n\n"
            "Please perform ONE targeted refinement step using appropriate tools to find missing "
            "details and return the updated final JSON."
        )
        messages.append(
            Message(
                role="assistant",
                content=Content.public(final_output_text) if allow_cloud else final_output_text,
            )
        )
        messages.append(
            Message(
                role="user",
                content=Content.public(refine_msg) if allow_cloud else refine_msg,
            )
        )

        refine_chunks: list[ChatChunk] = []
        try:
            async for chunk in router.chat(
                role=role, messages=messages, tools=tool_specs, stream=False
            ):
                refine_chunks.append(chunk)

            refine_tool_calls: list[ToolCall] = []
            for c in refine_chunks:
                if c.tool_calls:
                    refine_tool_calls.extend(c.tool_calls)

            # If refinement wants a tool call, run it
            if refine_tool_calls:
                call = refine_tool_calls[0]
                tool_ctx = ToolContext(
                    run_id=actual_run_id,
                    config=config,
                    allow_cloud=allow_cloud,
                    db=db,
                    context_taint=current_taint,
                    router=router,
                )
                res = await registry.execute(call.name, call.arguments or {}, tool_ctx)
                out_snippet = res.output[:MAX_TOOL_RESULT_CHARS]
                messages.append(
                    Message(
                        role="assistant",
                        content=Content.public("") if allow_cloud else "",
                        tool_calls=[call],
                    )
                )
                messages.append(
                    Message(
                        role="tool",
                        content=Content.public(out_snippet) if allow_cloud else out_snippet,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

                # Get final answer after tool call
                final_res = ""
                async for chunk in router.chat(role=role, messages=messages, stream=False):
                    if chunk.text:
                        final_res += chunk.text
                if final_res:
                    refine_output = parse_worker_json(final_res)
                    if node.kind == "research" and not refine_output.sources:
                        refine_output.confidence = min(refine_output.confidence, 0.3)
                    if refine_output.confidence >= output.confidence:
                        output = refine_output
            else:
                refine_text = "".join(c.text for c in refine_chunks if c.text)
                if refine_text:
                    refine_output = parse_worker_json(refine_text)
                    if node.kind == "research" and not refine_output.sources:
                        refine_output.confidence = min(refine_output.confidence, 0.3)
                    if refine_output.confidence >= output.confidence:
                        output = refine_output
        except Exception as exc:
            log.warning("Refinement pass failed for node %s: %s", node.id, exc)

    # Final safeguard: research nodes must provide sources per spec §6.4
    if node.kind == "research" and not output.sources:
        output.confidence = min(output.confidence, 0.3)

    return output
