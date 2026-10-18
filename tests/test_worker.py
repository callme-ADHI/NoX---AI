"""Tests for Worker agents executing DAG subtasks with tools and refinement (Section 6.4)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from noxai.agent.planner import SubtaskNode
from noxai.agent.worker import WorkerOutput, execute_worker_subtask
from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.tools.registry import ToolContext, ToolDefinition, ToolRegistry, ToolResult


class SearchParams(BaseModel):
    query: str


async def dummy_search(params: SearchParams, ctx: ToolContext) -> ToolResult:
    return ToolResult(
        ok=True,
        output="Result for " + params.query,
        summary=f"searched {params.query}",
        sources=[{"title": "Search Result", "url": "https://example.com/1"}],
    )


@pytest.fixture
def test_config() -> Settings:
    return Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="fake-brain"),
                "embed": ModelRoleConfig(provider="fake", model="fake-embed"),
            }
        )
    )


@pytest.fixture
def fake_router(test_config: Settings) -> tuple[ModelRouter, FakeProvider]:
    fake = FakeProvider()
    router = ModelRouter(config=test_config, providers={"fake": fake})
    return router, fake


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="web_search",
            description="Search the web",
            params_model=SearchParams,
            tier="read",
            func=dummy_search,
        )
    )
    return reg


@pytest.mark.asyncio
async def test_worker_successful_tool_call_and_summary(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
) -> None:
    """Worker executes tool call, receives result, and returns structured findings."""
    router, fake = fake_router

    # Step 1: Model calls web_search
    fake.queue_tool_call(name="web_search", arguments={"query": "kernel exploits"})
    # Step 2: Model finishes with valid WorkerOutput JSON
    summary_json = """
    {
      "summary": "Kernel exploits target use-after-free bugs.",
      "key_points": ["UAF is dominant", "eBPF verifier bypasses observed"],
      "sources": [{"title": "Kernel Bug", "url": "https://example.com/bug"}],
      "confidence": 0.9,
      "gaps": []
    }
    """
    fake.queue_response(summary_json)

    node = SubtaskNode(
        id="t1",
        title="Linux Kernel Bugs",
        objective="Find common exploit vectors in modern Linux kernels.",
        kind="research",
        expected_output="Summary of kernel vulnerabilities.",
    )

    output = await execute_worker_subtask(node, {}, router, registry, test_config)
    assert output.confidence == 0.9
    assert len(output.sources) >= 1
    assert "Kernel exploits" in output.summary


@pytest.mark.asyncio
async def test_worker_no_sources_caps_confidence(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
) -> None:
    """Research nodes with no sources are capped at confidence <= 0.3 per spec §6.4."""
    router, fake = fake_router

    summary_without_sources = """
    {
      "summary": "Some general overview without any web references.",
      "key_points": ["Point A"],
      "sources": [],
      "confidence": 0.85,
      "gaps": []
    }
    """
    fake.queue_response(summary_without_sources)
    fake.queue_response(summary_without_sources)

    node = SubtaskNode(
        id="t1",
        title="Unverified Research",
        objective="Look up undocumented protocol.",
        kind="research",
        expected_output="Findings.",
    )

    output = await execute_worker_subtask(node, {}, router, registry, test_config)
    # Must be capped at <= 0.3
    assert output.confidence <= 0.3


@pytest.mark.asyncio
async def test_worker_low_confidence_triggers_one_refinement(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
) -> None:
    """Nodes with confidence < 0.5 trigger exactly ONE refinement attempt."""
    router, fake = fake_router

    # Initial low confidence response
    low_conf = """
    {
      "summary": "Unclear findings.",
      "key_points": [],
      "sources": [],
      "confidence": 0.2,
      "gaps": ["Could not locate primary specification"]
    }
    """
    fake.queue_response(low_conf)

    # Refinement response with higher confidence
    improved = """
    {
      "summary": "Found the official RFC specification.",
      "key_points": ["RFC 9000 specifies QUIC"],
      "sources": [{"title": "RFC 9000", "url": "https://rfc-editor.org/rfc9000"}],
      "confidence": 0.95,
      "gaps": []
    }
    """
    fake.queue_response(improved)

    node = SubtaskNode(
        id="t1",
        title="QUIC Protocol",
        objective="Find QUIC RFC.",
        kind="research",
        expected_output="RFC details.",
    )

    output = await execute_worker_subtask(node, {}, router, registry, test_config)
    assert output.confidence == 0.95
    assert len(fake.calls) == 2  # initial + 1 refinement


@pytest.mark.asyncio
async def test_worker_text_tool_call_fallback(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
) -> None:
    """Plain text tool JSON is detected and executed via text fallback."""
    router, fake = fake_router

    # Step 1: Plain text tool call
    fake.queue_response('{"tool": "web_search", "args": {"query": "CVE-2024-1234"}}')
    # Step 2: Concluding JSON
    fake.queue_response("""
    {
      "summary": "CVE-2024-1234 is a remote code execution vulnerability.",
      "key_points": ["RCE in web service"],
      "sources": [{"title": "NVD", "url": "https://nvd.nist.gov/1234"}],
      "confidence": 0.85,
      "gaps": []
    }
    """)

    node = SubtaskNode(
        id="t1",
        title="CVE Details",
        objective="Find CVE info.",
        kind="research",
        expected_output="CVE findings.",
    )

    output = await execute_worker_subtask(node, {}, router, registry, test_config)
    assert "CVE-2024-1234" in output.summary
    assert len(output.sources) >= 1


@pytest.mark.asyncio
async def test_worker_plain_text_heuristic_fallback(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
) -> None:
    """If worker outputs non-JSON unstructured text, fallback creates valid WorkerOutput."""
    router, fake = fake_router
    plain = "Here is plain text findings without any JSON tags or structure."
    fake.queue_response(plain)
    fake.queue_response(plain)

    node = SubtaskNode(
        id="t1",
        title="Plain Text Output",
        objective="Generate answer.",
        kind="compute",
        expected_output="Plain answer.",
    )

    output = await execute_worker_subtask(node, {}, router, registry, test_config)
    assert isinstance(output, WorkerOutput)
    assert "plain text findings" in output.summary


@pytest.mark.asyncio
async def test_worker_dependency_summaries_injected(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
) -> None:
    """Dependency node findings are injected into the worker prompt."""
    router, fake = fake_router
    fake.queue_response("""
    {
      "summary": "Finished based on previous task findings.",
      "key_points": ["Synthesized"],
      "sources": [],
      "confidence": 0.8,
      "gaps": []
    }
    """)

    dep_output = WorkerOutput(
        summary="Prerequisite task discovered database credentials vulnerability.",
        key_points=["Hardcoded password in config.php"],
        sources=[],
        confidence=0.9,
    )

    node = SubtaskNode(
        id="t2",
        title="Remediation Strategy",
        objective="Propose remediation for credentials finding.",
        kind="compute",
        depends_on=["t1"],
        expected_output="Remediation plan.",
    )

    await execute_worker_subtask(node, {"t1": dep_output}, router, registry, test_config)
    call = fake.calls[0]
    prompt_content = call["messages"][-1].content
    assert "Prerequisite task discovered database credentials vulnerability" in prompt_content
    assert "Hardcoded password in config.php" in prompt_content
