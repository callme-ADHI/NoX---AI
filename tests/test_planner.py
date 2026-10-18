"""Tests for Strategic Planner DAG decomposition and validation (Section 6.4)."""

from __future__ import annotations

import pytest

from noxai.agent.planner import (
    Plan,
    PlanValidationError,
    SubtaskNode,
    generate_plan,
    validate_plan_dag,
)
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


VALID_PLAN_JSON = """
{
  "goal": "Research Quantum Computing Threats",
  "subtasks": [
    {
      "id": "t1",
      "title": "Shor's Algorithm Basics",
      "objective": "Explain how Shor's algorithm affects RSA and ECC cryptography.",
      "kind": "research",
      "depends_on": [],
      "suggested_tools": ["web_search"],
      "expected_output": "Summary of quantum algorithmic speedup on public key crypto."
    },
    {
      "id": "t2",
      "title": "Post-Quantum Cryptography Standards",
      "objective": "Identify NIST finalized PQC standards (ML-KEM, ML-DSA).",
      "kind": "research",
      "depends_on": ["t1"],
      "suggested_tools": ["web_search", "fetch_page"],
      "expected_output": "List of NIST post-quantum standards and timelines."
    },
    {
      "id": "t3",
      "title": "Transition Roadmap",
      "objective": "Outline recommendations for organizations transitioning to PQC.",
      "kind": "action",
      "depends_on": ["t2"],
      "suggested_tools": ["web_search"],
      "expected_output": "Step-by-step migration recommendations."
    }
  ],
  "synthesis_instructions": "Combine into an executive briefing with numbered citations."
}
"""


@pytest.mark.asyncio
async def test_generate_plan_valid_json(
    fake_router: tuple[ModelRouter, FakeProvider], test_config: Settings
) -> None:
    """Planner generates structured Plan from valid JSON."""
    router, fake = fake_router
    fake.queue_response(VALID_PLAN_JSON)

    plan = await generate_plan("Research Quantum Computing Threats", router, test_config)
    assert plan.goal == "Research Quantum Computing Threats"
    assert len(plan.subtasks) == 3
    assert plan.subtasks[0].id == "t1"
    assert plan.subtasks[1].depends_on == ["t1"]

    order = validate_plan_dag(plan)
    assert order == ["t1", "t2", "t3"]


@pytest.mark.asyncio
async def test_generate_plan_markdown_fence_repair(
    fake_router: tuple[ModelRouter, FakeProvider], test_config: Settings
) -> None:
    """Planner extracts JSON embedded within markdown code fences."""
    router, fake = fake_router
    fenced = f"Here is the plan:\n```json\n{VALID_PLAN_JSON}\n```\nHope this helps!"
    fake.queue_response(fenced)

    plan = await generate_plan("Research Quantum Computing Threats", router, test_config)
    assert len(plan.subtasks) == 3


def test_validate_plan_cycle_detection() -> None:
    """Plan with cyclical dependencies raises PlanValidationError."""
    plan = Plan(
        goal="Cyclic Plan",
        subtasks=[
            SubtaskNode(
                id="t1",
                title="1",
                objective="o1",
                kind="research",
                expected_output="e1",
                depends_on=["t2"],
            ),
            SubtaskNode(
                id="t2",
                title="2",
                objective="o2",
                kind="research",
                expected_output="e2",
                depends_on=["t1"],
            ),
        ],
        synthesis_instructions="Merge",
    )
    with pytest.raises(PlanValidationError, match="Cyclic"):
        validate_plan_dag(plan)


def test_validate_plan_max_subtasks_exceeded() -> None:
    """Plan exceeding max_subtasks raises PlanValidationError."""
    nodes = [
        SubtaskNode(
            id=f"t{i}",
            title=f"Task {i}",
            objective="o",
            kind="research",
            expected_output="e",
        )
        for i in range(10)
    ]
    plan = Plan(goal="Large Plan", subtasks=nodes, synthesis_instructions="Merge")
    with pytest.raises(PlanValidationError, match="maximum allowed"):
        validate_plan_dag(plan, max_subtasks=8)


def test_validate_plan_max_depth_exceeded() -> None:
    """Plan exceeding maximum dependency depth raises PlanValidationError."""
    # Chain of depth 4: t1 -> t2 -> t3 -> t4
    nodes = [
        SubtaskNode(
            id="t1",
            title="T1",
            objective="o",
            kind="research",
            expected_output="e",
        ),
        SubtaskNode(
            id="t2",
            title="T2",
            objective="o",
            kind="research",
            expected_output="e",
            depends_on=["t1"],
        ),
        SubtaskNode(
            id="t3",
            title="T3",
            objective="o",
            kind="research",
            expected_output="e",
            depends_on=["t2"],
        ),
        SubtaskNode(
            id="t4",
            title="T4",
            objective="o",
            kind="research",
            expected_output="e",
            depends_on=["t3"],
        ),
    ]
    plan = Plan(goal="Deep Plan", subtasks=nodes, synthesis_instructions="Merge")
    with pytest.raises(PlanValidationError, match="depth"):
        validate_plan_dag(plan, max_depth=2)


@pytest.mark.asyncio
async def test_planner_retries_and_recovers(
    fake_router: tuple[ModelRouter, FakeProvider], test_config: Settings
) -> None:
    """Planner provides feedback on validation failure and succeeds on retry."""
    router, fake = fake_router

    # Attempt 0: Invalid plan with cycle
    invalid_plan = """
    {
      "goal": "Test Goal",
      "subtasks": [
        {"id": "t1", "title": "1", "objective": "o", "expected_output": "e", "depends_on": ["t2"]},
        {"id": "t2", "title": "2", "objective": "o", "expected_output": "e", "depends_on": ["t1"]}
      ],
      "synthesis_instructions": "Merge"
    }
    """
    fake.queue_response(invalid_plan)
    # Attempt 1: Valid corrected plan
    fake.queue_response(VALID_PLAN_JSON)

    plan = await generate_plan("Test Goal", router, test_config)
    assert len(plan.subtasks) == 3
    # Two calls made to the model
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_planner_double_failure_fallback(
    fake_router: tuple[ModelRouter, FakeProvider], test_config: Settings
) -> None:
    """If model fails twice, planner falls back to single-node plan."""
    router, fake = fake_router
    fake.queue_response("Invalid text that is not json at all")
    fake.queue_response("Still not json")

    plan = await generate_plan("Fallback Goal", router, test_config)
    assert len(plan.subtasks) == 1
    assert plan.subtasks[0].id == "t1"
    assert "Fallback Goal" in plan.subtasks[0].objective


@pytest.mark.asyncio
async def test_planner_cloud_privacy_omits_memory(
    fake_router: tuple[ModelRouter, FakeProvider], test_config: Settings
) -> None:
    """Cloud-eligible runs route to heavy and omit memory context."""
    router, fake = fake_router
    fake.queue_response(VALID_PLAN_JSON)

    await generate_plan(
        goal="Public Cloud Research",
        router=router,
        config=test_config,
        allow_cloud=True,
        memory_context="Secret private user fact",
    )

    call = fake.calls[0]
    assert call["model"] == "fake-heavy"
    # Ensure memory context was omitted from prompt
    for msg in call["messages"]:
        assert "Secret private user fact" not in msg.content
