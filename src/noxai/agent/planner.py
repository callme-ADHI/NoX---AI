"""Planner module for decomposing complex goals into DAG subtasks per Section 6.4."""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator

from noxai.agent.json_repair import repair_json
from noxai.models.base import Message

if TYPE_CHECKING:
    from noxai.config import Settings
    from noxai.models.router import ModelRouter

log = logging.getLogger(__name__)

PLANNER_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "planner.md"


def _load_planner_prompt() -> str:
    if PLANNER_PROMPT_PATH.exists():
        return PLANNER_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "You are NoX's Strategic Planner. Decompose the goal into 3-7 subtasks in strict JSON.\n"
        'Schema: {"goal": str, "subtasks": [{"id": str, "title": str, "objective": str, '
        '"kind": str, "depends_on": [], "suggested_tools": [], "expected_output": str}], '
        '"synthesis_instructions": str}'
    )


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class SubtaskNode(BaseModel):
    """Single node in the execution DAG."""

    id: str = Field(..., description="Unique subtask identifier, e.g. 't1', 't2'.")
    title: str = Field(..., description="Short descriptive title.")
    objective: str = Field(..., description="Self-contained execution instruction.")
    kind: Literal["research", "fetch", "compute", "action"] = Field(
        "research",
        description="Type of task: research, fetch, compute, action.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="List of prerequisite subtask IDs.",
    )
    suggested_tools: list[str] = Field(
        default_factory=list,
        description="Suggested tool names for this node.",
    )
    expected_output: str = Field(
        ...,
        description="Specification of the required findings.",
    )

    @field_validator("id")
    @classmethod
    def clean_id(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Subtask id cannot be empty.")
        return s


class Plan(BaseModel):
    """Complete decomposed plan ready for DAG execution."""

    goal: str = Field(..., description="The high-level goal being achieved.")
    subtasks: list[SubtaskNode] = Field(
        ...,
        min_length=1,
        description="Ordered or DAG list of subtask nodes.",
    )
    synthesis_instructions: str = Field(
        ...,
        description="Guidelines for synthesizing the final output.",
    )


# ---------------------------------------------------------------------------
# DAG Validation
# ---------------------------------------------------------------------------


class PlanValidationError(ValueError):
    """Raised when a plan DAG has cycles, missing dependencies, or excessive depth."""


def validate_plan_dag(plan: Plan, max_subtasks: int = 8, max_depth: int = 3) -> list[str]:
    """Validate DAG acyclicity, dependency existence, and tree depth.

    Returns:
        Topologically sorted list of subtask IDs.
    Raises:
        PlanValidationError if cycles, invalid dependencies, or excessive depth exist.
    """
    if len(plan.subtasks) > max_subtasks:
        raise PlanValidationError(
            f"Plan contains {len(plan.subtasks)} subtasks; maximum allowed is {max_subtasks}."
        )

    node_ids = {node.id for node in plan.subtasks}
    if len(node_ids) != len(plan.subtasks):
        raise PlanValidationError("Duplicate subtask IDs detected in plan.")

    in_degree: dict[str, int] = dict.fromkeys(node_ids, 0)
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}

    # Verify dependencies
    for node in plan.subtasks:
        for dep in node.depends_on:
            if dep == node.id:
                raise PlanValidationError(f"Subtask '{node.id}' cannot depend on itself.")
            if dep not in node_ids:
                raise PlanValidationError(
                    f"Subtask '{node.id}' depends on non-existent node '{dep}'."
                )
            adj[dep].append(node.id)
            in_degree[node.id] += 1

    # Kahn's topological sort
    queue: deque[str] = deque([nid for nid, deg in in_degree.items() if deg == 0])
    topo_order: list[str] = []
    depths: dict[str, int] = dict.fromkeys(node_ids, 0)

    while queue:
        curr = queue.popleft()
        topo_order.append(curr)

        for neighbor in adj[curr]:
            depths[neighbor] = max(depths[neighbor], depths[curr] + 1)
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(topo_order) != len(node_ids):
        raise PlanValidationError("Cyclic dependency detected in plan subtasks.")

    max_found_depth = max(depths.values()) if depths else 0
    if max_found_depth > max_depth:
        raise PlanValidationError(
            f"Plan DAG depth of {max_found_depth} exceeds maximum depth limit of {max_depth}."
        )

    return topo_order


def make_fallback_plan(goal: str) -> Plan:
    """Create a single-node fallback plan when LLM generation fails twice."""
    return Plan(
        goal=goal,
        subtasks=[
            SubtaskNode(
                id="t1",
                title="Execute Request",
                objective=f"Research and answer the following request thoroughly: {goal}",
                kind="research",
                depends_on=[],
                suggested_tools=["web_search", "fetch_page"],
                expected_output="A thorough, factual breakdown of the topic with sources.",
            )
        ],
        synthesis_instructions="Present a clear, well-structured final answer with citations.",
    )


# ---------------------------------------------------------------------------
# Planner Execution
# ---------------------------------------------------------------------------


async def generate_plan(
    goal: str,
    router: ModelRouter,
    config: Settings,
    allow_cloud: bool = False,
    memory_context: str | None = None,
) -> Plan:
    """Generate and validate a DAG execution plan for a complex goal.

    Uses role 'heavy' if cloud-eligible, else 'brain'.
    Includes 1-attempt error feedback retry on failure, falling back to single-node.
    """
    planner_system = _load_planner_prompt()
    role = "heavy" if allow_cloud else "brain"

    # Context assembly
    user_content_parts = [f"Owner's Goal: {goal}"]
    if not allow_cloud and memory_context:
        user_content_parts.append(f"\nRelevant Owner Memory / Context:\n{memory_context}")

    messages = [
        Message(role="system", content=planner_system),
        Message(role="user", content="\n".join(user_content_parts)),
    ]

    max_subtasks = config.agent.max_subtasks
    max_depth = config.agent.max_depth

    for attempt in range(2):
        raw_text = ""
        try:
            async for chunk in router.chat(role=role, messages=messages, stream=False):
                if chunk.text:
                    raw_text += chunk.text
        except Exception as exc:
            log.warning("Planner LLM call failed (attempt %d): %s", attempt + 1, exc)
            if attempt == 1:
                return make_fallback_plan(goal)
            continue

        clean = raw_text.strip()
        if "```json" in clean:
            clean = clean.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in clean:
            clean = clean.split("```", 1)[1].split("```", 1)[0].strip()

        # Parse JSON
        parsed: dict[str, Any] | None = None
        try:
            parsed = json.loads(clean)
        except Exception:
            try:
                parsed = repair_json(clean)
            except Exception as parse_err:
                log.warning("Planner JSON parsing failed: %s", parse_err)

        if isinstance(parsed, dict):
            try:
                # If LLM didn't return a goal, provide the user goal
                if "goal" not in parsed or not parsed["goal"]:
                    parsed["goal"] = goal
                candidate_plan = Plan.model_validate(parsed)
                validate_plan_dag(candidate_plan, max_subtasks=max_subtasks, max_depth=max_depth)
                return candidate_plan
            except Exception as val_err:
                log.warning("Plan validation error on attempt %d: %s", attempt + 1, val_err)
                if attempt == 0:
                    # Provide feedback to the model once
                    messages.append(Message(role="assistant", content=raw_text))
                    messages.append(
                        Message(
                            role="user",
                            content=(
                                f"Your plan had a validation error: {val_err}\n"
                                "Please fix the error and output the corrected JSON plan\n"
                                f"conforming strictly to schema (max {max_subtasks} subtasks, "
                                f"max depth {max_depth}, no cycles)."
                            ),
                        )
                    )
        else:
            if attempt == 0:
                messages.append(Message(role="assistant", content=raw_text))
                messages.append(
                    Message(
                        role="user",
                        content=(
                            "Your response was not valid JSON. "
                            "Please return ONLY a valid JSON plan."
                        ),
                    )
                )

    log.warning("Planner failed twice; using single-node fallback plan.")
    return make_fallback_plan(goal)
