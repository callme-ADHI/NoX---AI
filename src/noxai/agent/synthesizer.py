"""Synthesizer module for aggregating worker node outputs into a cited final report."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from noxai.models.base import Message

if TYPE_CHECKING:
    from noxai.agent.planner import SubtaskNode
    from noxai.agent.worker import WorkerOutput
    from noxai.models.router import ModelRouter

log = logging.getLogger(__name__)

SYNTHESIZER_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "synthesizer.md"


def _load_synthesizer_prompt() -> str:
    if SYNTHESIZER_PROMPT_PATH.exists():
        return SYNTHESIZER_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "You are NoX's Synthesizer. Merge worker findings into a coherent, structured report with "
        "numbered citations [1], [2], an appended ## Sources section, and a ## Gaps & Limitations section."
    )


@dataclass
class SynthesizerResult:
    """Outcome of synthesis generation."""

    text: str
    citations: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)


def extract_cited_urls(text: str) -> list[str]:
    """Extract URLs from markdown links or direct links in text."""
    urls = re.findall(r"https?://[^\s)\]]+", text)
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        clean = u.rstrip(".,;\"'")
        if clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    return deduped


async def synthesize_report(
    goal: str,
    synthesis_instructions: str,
    node_results: list[tuple[SubtaskNode, WorkerOutput | None, str | None]],
    router: ModelRouter,
    allow_cloud: bool = False,
    stream: bool = False,
) -> SynthesizerResult:
    """Synthesize completed and failed node outputs into a final cited report."""
    synthesizer_system = _load_synthesizer_prompt()
    role = "heavy" if allow_cloud else "brain"

    prompt_lines = [
        f"Goal: {goal}",
        f"Synthesis Instructions: {synthesis_instructions}\n",
        "=== Worker Findings ===",
    ]

    all_sources: list[dict[str, str]] = []
    missing_sections: list[str] = []

    for node, output, err in node_results:
        if output is not None and not err:
            prompt_lines.append(f"\n### Subtask [{node.id}]: {node.title} ({node.kind})")
            prompt_lines.append(f"Summary: {output.summary}")
            if output.key_points:
                prompt_lines.append("Key points:")
                for kp in output.key_points:
                    prompt_lines.append(f"- {kp}")
            if output.sources:
                prompt_lines.append("Sources collected:")
                for s in output.sources:
                    prompt_lines.append(f"- {s.title}: {s.url}")
                    all_sources.append({"title": s.title, "url": s.url})
            if output.gaps:
                prompt_lines.append(f"Reported gaps: {'; '.join(output.gaps)}")
        else:
            missing_sections.append(f"[{node.id}] {node.title}: {err or 'Timed out or failed'}")

    if missing_sections:
        prompt_lines.append("\n=== Missing or Failed Subtasks ===")
        for ms in missing_sections:
            prompt_lines.append(f"- {ms}")
        prompt_lines.append(
            "\nPlease state clearly in a '## Gaps & Limitations' section that the above areas "
            "could not be completed."
        )

    messages = [
        Message(role="system", content=synthesizer_system),
        Message(role="user", content="\n".join(prompt_lines)),
    ]

    final_text = ""
    try:
        async for chunk in router.chat(role=role, messages=messages, stream=stream):
            if chunk.text:
                final_text += chunk.text
    except Exception as exc:
        log.error("Synthesizer model call failed: %s", exc)
        # Fallback synthesis: directly concatenate worker summaries
        fallback_parts = [f"# Report: {goal}\n"]
        for node, output, err in node_results:
            if output:
                fallback_parts.append(f"## {node.title}\n{output.summary}\n")
        if missing_sections:
            fallback_parts.append("## Gaps & Limitations\n")
            for ms in missing_sections:
                fallback_parts.append(f"- {ms}\n")
        final_text = "\n".join(fallback_parts)

    citations = extract_cited_urls(final_text)
    return SynthesizerResult(
        text=final_text,
        citations=citations,
        sources=all_sources,
    )


async def synthesize_stream(
    goal: str,
    synthesis_instructions: str,
    node_results: list[tuple[SubtaskNode, WorkerOutput | None, str | None]],
    router: ModelRouter,
    allow_cloud: bool = False,
) -> AsyncIterator[str]:
    """Stream synthesized output chunks for live UI rendering."""
    synthesizer_system = _load_synthesizer_prompt()
    role = "heavy" if allow_cloud else "brain"

    prompt_lines = [
        f"Goal: {goal}",
        f"Synthesis Instructions: {synthesis_instructions}\n",
        "=== Worker Findings ===",
    ]

    for node, output, err in node_results:
        if output is not None and not err:
            prompt_lines.append(f"\n### Subtask [{node.id}]: {node.title}")
            prompt_lines.append(f"Summary: {output.summary}")
            if output.sources:
                prompt_lines.append("Sources collected:")
                for s in output.sources:
                    prompt_lines.append(f"- {s.title}: {s.url}")
        else:
            prompt_lines.append(f"\n### Subtask [{node.id}]: {node.title} - FAILED ({err})")

    messages = [
        Message(role="system", content=synthesizer_system),
        Message(role="user", content="\n".join(prompt_lines)),
    ]

    async for chunk in router.chat(role=role, messages=messages, stream=True):
        if chunk.text:
            yield chunk.text
