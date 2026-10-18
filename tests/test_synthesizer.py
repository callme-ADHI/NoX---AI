"""Tests for Synthesizer module (Section 6.4)."""

from __future__ import annotations

import pytest

from noxai.agent.planner import SubtaskNode
from noxai.agent.synthesizer import (
    extract_cited_urls,
    synthesize_report,
    synthesize_stream,
)
from noxai.agent.worker import WorkerOutput, WorkerSource
from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter


@pytest.fixture
def test_config() -> Settings:
    return Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="fake-brain"),
                "heavy": ModelRoleConfig(provider="fake", model="fake-heavy"),
                "embed": ModelRoleConfig(provider="fake", model="fake-embed"),
            }
        )
    )


@pytest.fixture
def fake_router(test_config: Settings) -> tuple[ModelRouter, FakeProvider]:
    fake = FakeProvider()
    router = ModelRouter(config=test_config, providers={"fake": fake})
    return router, fake


def _make_sample_node(node_id: str, title: str) -> SubtaskNode:
    return SubtaskNode(
        id=node_id,
        title=title,
        objective=f"Objective for {title}",
        kind="research",
        expected_output=f"Output for {title}",
    )


def test_extract_cited_urls_deduplicates_and_cleans() -> None:
    text = (
        "Check https://example.com/a, and also https://example.com/b. "
        "Again https://example.com/a and https://example.com/c; please verify."
    )
    urls = extract_cited_urls(text)
    assert urls == ["https://example.com/a", "https://example.com/b", "https://example.com/c"]


@pytest.mark.asyncio
async def test_synthesizer_report_with_citations(
    fake_router: tuple[ModelRouter, FakeProvider],
) -> None:
    router, fake = fake_router

    report_text = (
        "# Security Analysis\n\n"
        "Recent kernel patches address race conditions [1]. "
        "Firmware verification is also required [2].\n\n"
        "## Sources\n"
        "[1] https://kernel.org/patch1\n"
        "[2] https://firmware.org/spec\n"
    )
    fake.queue_response(report_text)

    node1 = _make_sample_node("n1", "Kernel Patches")
    out1 = WorkerOutput(
        summary="Kernel patches mitigate UAF.",
        sources=[WorkerSource(title="Kernel Patch", url="https://kernel.org/patch1")],
        confidence=0.9,
    )

    node2 = _make_sample_node("n2", "Firmware Spec")
    out2 = WorkerOutput(
        summary="Firmware requires signature verification.",
        sources=[WorkerSource(title="Firmware Spec", url="https://firmware.org/spec")],
        confidence=0.85,
    )

    res = await synthesize_report(
        goal="Audit modern Linux security features",
        synthesis_instructions="Provide numbered citations and a Sources section.",
        node_results=[(node1, out1, None), (node2, out2, None)],
        router=router,
        allow_cloud=False,
    )

    assert "Recent kernel patches" in res.text
    assert "https://kernel.org/patch1" in res.citations
    assert "https://firmware.org/spec" in res.citations
    assert len(res.sources) == 2


@pytest.mark.asyncio
async def test_synthesizer_sources_deduplication(
    fake_router: tuple[ModelRouter, FakeProvider],
) -> None:
    router, fake = fake_router
    fake.queue_response("Report text referring to https://docs.python.org/3/whatsnew.")

    node1 = _make_sample_node("n1", "Python 3.14 Features")
    out1 = WorkerOutput(
        summary="Summary A",
        confidence=0.8,
        sources=[WorkerSource(title="Python Docs", url="https://docs.python.org/3/whatsnew")],
    )
    node2 = _make_sample_node("n2", "Python Standard Library")
    out2 = WorkerOutput(
        summary="Summary B",
        confidence=0.8,
        sources=[WorkerSource(title="Python Docs Dupe", url="https://docs.python.org/3/whatsnew")],
    )

    res = await synthesize_report(
        goal="Python 3.14 Overview",
        synthesis_instructions="Summarize new features.",
        node_results=[(node1, out1, None), (node2, out2, None)],
        router=router,
    )

    assert len(res.citations) == 1
    assert res.citations[0] == "https://docs.python.org/3/whatsnew"


@pytest.mark.asyncio
async def test_synthesizer_gaps_and_failures_reported(
    fake_router: tuple[ModelRouter, FakeProvider],
) -> None:
    router, fake = fake_router

    fake.queue_response(
        "# Findings\n\nPart 1 succeeded.\n\n"
        "## Gaps & Limitations\nPart 2 timed out and could not be evaluated."
    )

    node1 = _make_sample_node("n1", "Completed Node")
    out1 = WorkerOutput(summary="Finished analysis.", confidence=0.8)
    node2 = _make_sample_node("n2", "Failed Node")

    res = await synthesize_report(
        goal="Comprehensive Test",
        synthesis_instructions="Report findings and limitations.",
        node_results=[(node1, out1, None), (node2, None, "Connection timeout")],
        router=router,
    )

    assert "Gaps & Limitations" in res.text
    # Check that failed node was mentioned in user prompt sent to router
    assert len(fake.calls) == 1
    user_prompt = fake.calls[0]["messages"][-1].get_text()
    assert "Missing or Failed Subtasks" in user_prompt
    assert "Connection timeout" in user_prompt


@pytest.mark.asyncio
async def test_synthesizer_fallback_on_model_error(
    fake_router: tuple[ModelRouter, FakeProvider],
) -> None:
    router, fake = fake_router
    # Queue an exception to simulate LLM failure
    fake.queue_response(RuntimeError("LLM service unavailable"))

    node1 = _make_sample_node("n1", "Worker Section 1")
    out1 = WorkerOutput(summary="Critical findings from worker 1.", confidence=0.8)
    node2 = _make_sample_node("n2", "Worker Section 2")

    res = await synthesize_report(
        goal="Assess system state",
        synthesis_instructions="Create report.",
        node_results=[(node1, out1, None), (node2, None, "Worker error")],
        router=router,
    )

    # Fallback should assemble worker summaries without crashing
    assert "# Report: Assess system state" in res.text
    assert "Critical findings from worker 1." in res.text
    assert "## Gaps & Limitations" in res.text
    assert "Worker error" in res.text


@pytest.mark.asyncio
async def test_synthesizer_cloud_vs_local_role(
    fake_router: tuple[ModelRouter, FakeProvider],
) -> None:
    router, fake = fake_router

    node = _make_sample_node("n1", "Node")
    out = WorkerOutput(summary="Summary text", confidence=0.8)

    # Call 1: allow_cloud=False -> role="brain"
    fake.queue_response("Local response")
    await synthesize_report(
        goal="Goal 1",
        synthesis_instructions="Instructions",
        node_results=[(node, out, None)],
        router=router,
        allow_cloud=False,
    )
    assert fake.calls[-1]["model"] == "fake-brain"

    # Call 2: allow_cloud=True -> role="heavy"
    fake.queue_response("Cloud response")
    await synthesize_report(
        goal="Goal 2",
        synthesis_instructions="Instructions",
        node_results=[(node, out, None)],
        router=router,
        allow_cloud=True,
    )
    assert fake.calls[-1]["model"] == "fake-heavy"


@pytest.mark.asyncio
async def test_synthesize_stream_yields_chunks(
    fake_router: tuple[ModelRouter, FakeProvider],
) -> None:
    router, fake = fake_router
    from noxai.models.base import ChatChunk

    chunks = [
        ChatChunk(delta_text="Chunk 1 "),
        ChatChunk(delta_text="Chunk 2 "),
        ChatChunk(delta_text="Chunk 3"),
    ]
    fake.queue_response(chunks)

    node = _make_sample_node("n1", "Node")
    out = WorkerOutput(summary="Summary text", confidence=0.8)

    collected: list[str] = []
    async for text_chunk in synthesize_stream(
        goal="Streaming Goal",
        synthesis_instructions="Stream instructions",
        node_results=[(node, out, None)],
        router=router,
    ):
        collected.append(text_chunk)

    assert "".join(collected) == "Chunk 1 Chunk 2 Chunk 3"
