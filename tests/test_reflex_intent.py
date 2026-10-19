"""Tests for Reflex Router, zero-LLM intent analysis, and Front Desk dispatch tools (§1.4, §G3)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from noxai.agent.loop import FRONT_DESK_TOOLS, AgentLoop
from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.db import execute, fetch_one, get_db, run_migrations
from noxai.events import EventBus
from noxai.models.base import ChatChunk
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.router.intent import IntentType, ReplyMode, analyze_intent
from noxai.tools.builtin.dispatch import (
    DispatchJobEditParams,
    DispatchReminderParams,
    DispatchTaskParams,
    RequestApprovalParams,
    dispatch_job_edit,
    dispatch_reminder,
    dispatch_task,
    request_approval,
)
from noxai.tools.registry import ToolContext, registry

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def make_test_settings() -> Settings:
    return Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="fake-model"),
                "embed": ModelRoleConfig(provider="fake", model="fake-model"),
            }
        )
    )


def make_test_router(
    text: str = "Instant response from Front Desk", cfg: Settings | None = None
) -> tuple[ModelRouter, FakeProvider]:
    cfg = cfg or make_test_settings()
    fake = FakeProvider()
    fake.queue_response([ChatChunk(text=text)])
    router = ModelRouter(config=cfg, providers={"fake": fake})
    return router, fake


# ---------------------------------------------------------------------------
# 1. Zero-LLM Intent Analysis: All 10 Categories & Reply Modes
# ---------------------------------------------------------------------------


class TestReflexIntentAnalysis:
    def test_reply_now_intent(self) -> None:
        queries = [
            "What is the airspeed velocity of an unladen swallow?",
            "Hello there, how are you doing today?",
            "Explain quantum superposition in simple terms.",
        ]
        for q in queries:
            res = analyze_intent(q)
            assert res.intent == IntentType.REPLY_NOW
            assert res.reply_mode == ReplyMode.INSTANT
            assert res.suggested_lane == "cloud"
            assert res.confidence >= 0.8

    def test_reminder_intent(self) -> None:
        res = analyze_intent("Remind me to check the oven in 15 minutes")
        assert res.intent == IntentType.REMINDER
        assert res.reply_mode == ReplyMode.BOTH
        assert res.suggested_lane == "local"
        assert res.entities.get("delay_seconds") == 900

        res2 = analyze_intent("Set a timer for 30 seconds")
        assert res2.intent == IntentType.REMINDER
        assert res2.entities.get("delay_seconds") == 30

    def test_schedule_intent(self) -> None:
        queries = [
            "Schedule a daily briefing at 8am",
            "Every monday at 9am run the system backup",
            "Daily briefing at 7:30am with news summary",
        ]
        for q in queries:
            res = analyze_intent(q)
            assert res.intent == IntentType.SCHEDULE
            assert res.reply_mode == ReplyMode.BOTH
            assert res.suggested_lane == "local"

    def test_job_edit_intent(self) -> None:
        res_cancel = analyze_intent("Cancel the morning briefing job")
        assert res_cancel.intent == IntentType.JOB_EDIT
        assert res_cancel.reply_mode == ReplyMode.BOTH

        res_pause = analyze_intent("Pause scheduled task backup-01")
        assert res_pause.intent == IntentType.JOB_EDIT

        res_list = analyze_intent("List all my scheduled jobs")
        assert res_list.intent == IntentType.JOB_EDIT
        assert res_list.reply_mode == ReplyMode.INSTANT

    def test_background_task_intent(self) -> None:
        queries = [
            "Run this calculation in the background",
            "Please execute this long batch process asynchronously",
        ]
        for q in queries:
            res = analyze_intent(q)
            assert res.intent == IntentType.BACKGROUND_TASK
            assert res.reply_mode == ReplyMode.BOTH
            assert res.suggested_lane == "local"

    def test_deep_research_intent(self) -> None:
        queries = [
            "Conduct deep research on current breakthroughs in solid state batteries",
            "Give me a comprehensive report and literature review on transformer architectures",
        ]
        for q in queries:
            res = analyze_intent(q)
            assert res.intent == IntentType.DEEP_RESEARCH
            assert res.reply_mode == ReplyMode.BOTH
            assert res.suggested_lane == "cloud"

    def test_memory_op_intent(self) -> None:
        res1 = analyze_intent("Remember that my dog's name is Barnaby")
        assert res1.intent == IntentType.MEMORY_OP
        assert res1.reply_mode == ReplyMode.INSTANT
        assert res1.suggested_lane == "local"

        res2 = analyze_intent("What do you remember about my dog?")
        assert res2.intent == IntentType.MEMORY_OP

        res3 = analyze_intent("Forget my home address")
        assert res3.intent == IntentType.MEMORY_OP

    def test_system_action_intent(self) -> None:
        res_app = analyze_intent("Open firefox")
        assert res_app.intent == IntentType.SYSTEM_ACTION
        assert res_app.suggested_lane == "local"

        res_info = analyze_intent("What is my system info and battery status?")
        assert res_info.intent == IntentType.SYSTEM_ACTION
        assert res_info.reply_mode == ReplyMode.INSTANT

        res_run = analyze_intent("Run shell command df -h")
        assert res_run.intent == IntentType.SYSTEM_ACTION
        assert res_run.reply_mode == ReplyMode.BOTH

    def test_sensitive_intent(self) -> None:
        # Uses allowlisted fake key pattern from tests/test_repo_hygiene.py
        res_key = analyze_intent("Here is my secret token: sk-abcdef1234567890abcdef1234")
        assert res_key.intent == IntentType.SENSITIVE
        assert res_key.reply_mode == ReplyMode.INSTANT
        assert res_key.suggested_lane == "local"

        res_pass = analyze_intent("password = 'superSecretAdminPassword!'")
        assert res_pass.intent == IntentType.SENSITIVE
        assert res_pass.suggested_lane == "local"

    def test_ambiguous_intent(self) -> None:
        queries = ["", "   ", "x", "?", "help", "what"]
        for q in queries:
            res = analyze_intent(q)
            assert res.intent == IntentType.AMBIGUOUS
            assert res.reply_mode == ReplyMode.INSTANT


# ---------------------------------------------------------------------------
# 2. Benchmark Guarantee: <50ms Zero-LLM Intent Analysis
# ---------------------------------------------------------------------------


class TestReflexLatencyGuarantee:
    def test_reflex_sub_50ms_execution(self) -> None:
        """Every call to analyze_intent must run in under 50ms (§1.4, §G3)."""
        benchmark_queries = [
            "What is the theory of general relativity?",
            "Remind me in 30 minutes to drink water",
            "Schedule a morning briefing every day at 7am",
            "Pause the weather update job",
            "Run the full repository index in the background",
            "Deep research into solid state battery economics",
            "Remember that my favorite programming language is Python",
            "Open terminal and show battery status",
            "password = 'testPassword123'",
            "help",
        ]

        # Warm-up pass
        for q in benchmark_queries:
            analyze_intent(q)

        # Timed benchmark: 100 queries
        durations_ms: list[float] = []
        for _ in range(10):
            for q in benchmark_queries:
                t0 = time.perf_counter()
                res = analyze_intent(q)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                durations_ms.append(elapsed_ms)
                assert elapsed_ms < 50.0, f"Query '{q}' took {elapsed_ms:.2f}ms (>50ms)"
                assert res.analysis_time_ms < 50.0

        avg_ms = sum(durations_ms) / len(durations_ms)
        max_ms = max(durations_ms)
        # Typically < 1ms on modern CPU
        assert avg_ms < 5.0, f"Average latency {avg_ms:.2f}ms exceeded 5ms"
        assert max_ms < 20.0, f"Max latency {max_ms:.2f}ms exceeded 20ms"


# ---------------------------------------------------------------------------
# 3. Front Desk Tool Filtering
# ---------------------------------------------------------------------------


class TestFrontDeskToolFiltering:
    def test_front_desk_tools_allowlist(self) -> None:
        expected = {
            "web_search",
            "fetch_page",
            "dispatch_task",
            "dispatch_reminder",
            "dispatch_job_edit",
            "request_approval",
        }
        assert FRONT_DESK_TOOLS == expected

    @pytest.mark.asyncio
    async def test_front_desk_lane_limits_exposed_tools(self) -> None:
        cfg = make_test_settings()
        captured_tools: list[Any] = []

        async def capture_chat(*args: Any, **kwargs: Any) -> Any:
            captured_tools.extend(kwargs.get("tools") or [])
            yield ChatChunk(text="OK")

        fake = FakeProvider()
        fake.chat = capture_chat  # type: ignore[assignment]
        router = ModelRouter(config=cfg, providers={"fake": fake})

        loop = AgentLoop(config=cfg, router=router, registry=registry)
        await loop.run("Quick front desk request", lane="front_desk")

        exposed_tool_names = {t.name for t in captured_tools}
        assert exposed_tool_names.issubset(FRONT_DESK_TOOLS)
        assert "run_shell" not in exposed_tool_names


# ---------------------------------------------------------------------------
# 4. Front Desk Dispatch Tools Execution
# ---------------------------------------------------------------------------


class TestDispatchTools:
    @pytest.mark.asyncio
    async def test_dispatch_task(self) -> None:
        events: list[dict[str, Any]] = []

        def emit(evt: dict[str, Any]) -> None:
            events.append(evt)

        ctx = ToolContext(
            run_id="run_test_task",
            config=make_test_settings(),
            emit=emit,
        )

        params = DispatchTaskParams(
            title="Download datasets",
            prompt="Download and verify ImageNet archive",
            lane="local",
            priority="high",
        )
        res = await dispatch_task(params, ctx)
        assert res.ok is True
        assert "Download datasets" in res.output
        assert "priority=high" in res.output
        assert len(events) == 1
        assert events[0]["type"] == "task_dispatched"
        assert events[0]["title"] == "Download datasets"

    @pytest.mark.asyncio
    async def test_dispatch_reminder(self) -> None:
        await run_migrations()
        events: list[dict[str, Any]] = []

        def emit(evt: dict[str, Any]) -> None:
            events.append(evt)

        async with get_db() as db:
            ctx = ToolContext(
                run_id="run_test_rem",
                config=make_test_settings(),
                db=db,
                emit=emit,
            )
            params = DispatchReminderParams(
                reminder_text="Check the oven",
                delay_seconds=120,
            )
            res = await dispatch_reminder(params, ctx)
            assert res.ok is True
            assert "Reminder set for in 120 seconds" in res.output
            assert len(events) == 1
            assert events[0]["type"] == "reminder_dispatched"

            # Verify persisted job in DB
            row = await fetch_one(
                db, "SELECT * FROM jobs WHERE instruction = ?", ("Check the oven",)
            )
            assert row is not None
            assert row["schedule_type"] == "once"

    @pytest.mark.asyncio
    async def test_dispatch_job_edit(self) -> None:
        await run_migrations()
        async with get_db() as db:
            # Seed a test job
            await execute(
                db,
                """
                INSERT INTO jobs (id, name, description, schedule_type, schedule_expr, timezone,
                                  instruction, enabled, created_by, version)
                VALUES ('test_job_1', 'daily-sync', 'Daily sync', 'cron', '0 9 * * *', 'UTC',
                        'sync files', 1, 'agent', 1)
                """,
            )
            await db.commit()

            ctx = ToolContext(run_id="run_job_edit", config=make_test_settings(), db=db)

            # 1. Pause
            res_pause = await dispatch_job_edit(
                DispatchJobEditParams(job_id="test_job_1", action="pause"), ctx
            )
            assert res_pause.ok is True
            job = await fetch_one(db, "SELECT enabled FROM jobs WHERE id = 'test_job_1'")
            assert job is not None and job["enabled"] == 0

            # 2. Resume
            res_resume = await dispatch_job_edit(
                DispatchJobEditParams(job_id="test_job_1", action="resume"), ctx
            )
            assert res_resume.ok is True
            job = await fetch_one(db, "SELECT enabled FROM jobs WHERE id = 'test_job_1'")
            assert job is not None and job["enabled"] == 1

            # 3. Reschedule
            res_edit = await dispatch_job_edit(
                DispatchJobEditParams(
                    job_id="test_job_1", action="edit", new_schedule="daily at 6pm"
                ),
                ctx,
            )
            assert res_edit.ok is True
            job = await fetch_one(db, "SELECT schedule_expr FROM jobs WHERE id = 'test_job_1'")
            assert job is not None and "18" in job["schedule_expr"]

            # 4. Cancel / Delete
            res_del = await dispatch_job_edit(
                DispatchJobEditParams(job_id="test_job_1", action="cancel"), ctx
            )
            assert res_del.ok is True
            job = await fetch_one(db, "SELECT * FROM jobs WHERE id = 'test_job_1'")
            assert job is None

    @pytest.mark.asyncio
    async def test_request_approval(self) -> None:
        await run_migrations()

        # Case A: Owner approval callback granted
        hook_mock = AsyncMock(return_value=True)
        ctx_hook = ToolContext(
            run_id="run_appr_1",
            config=make_test_settings(),
            request_approval=hook_mock,
        )
        res_granted = await request_approval(
            RequestApprovalParams(
                action="reboot_system",
                reason="Kernel update completed",
            ),
            ctx_hook,
        )
        assert res_granted.ok is True
        assert "Approval granted" in res_granted.output
        hook_mock.assert_called_once()

        # Case B: Owner approval callback denied
        hook_mock_denied = AsyncMock(return_value=False)
        ctx_denied = ToolContext(
            run_id="run_appr_2",
            config=make_test_settings(),
            request_approval=hook_mock_denied,
        )
        res_denied = await request_approval(
            RequestApprovalParams(
                action="format_disk",
                reason="Storage reset",
            ),
            ctx_denied,
        )
        assert res_denied.ok is False
        assert "denied by owner" in res_denied.output

        # Case C: No hook provided, persisted to DB
        async with get_db() as db:
            ctx_db = ToolContext(
                run_id="run_appr_3",
                config=make_test_settings(),
                db=db,
            )
            res_parked = await request_approval(
                RequestApprovalParams(
                    action="wipe_cache",
                    reason="Free disk space",
                    details={"target": "/var/tmp"},
                ),
                ctx_db,
            )
            assert res_parked.ok is True
            assert "submitted for owner approval" in res_parked.output

            # Check DB record
            row = await fetch_one(
                db, "SELECT * FROM approvals WHERE tool_name = 'wipe_cache' AND status = 'pending'"
            )
            assert row is not None
            assert row["reason"] == "Free disk space"


# ---------------------------------------------------------------------------
# 5. AgentLoop Sensitive Intent and Trace Tests
# ---------------------------------------------------------------------------


class TestAgentLoopReflexWiring:
    @pytest.mark.asyncio
    async def test_sensitive_intent_forces_local_and_publishes_event(self) -> None:
        await run_migrations()
        cfg = make_test_settings()
        event_bus = EventBus()
        captured_events: list[dict[str, Any]] = []

        async def handler(evt: Any) -> None:
            if getattr(evt, "type", None) == "intent_detected":
                captured_events.append(evt.data)

        event_bus.subscribe(handler)

        router, _ = make_test_router("Local response", cfg=cfg)
        loop = AgentLoop(config=cfg, router=router, event_bus=event_bus)

        # Sensitive prompt with allowlisted dummy key
        prompt = "My API key is sk-abcdef1234567890abcdef1234. Please keep it safe."
        result = await loop.run(prompt, allow_cloud=True)

        assert result.status == "done"
        assert result.trace.get("intent") == IntentType.SENSITIVE.value

        # Event should have fired
        assert len(captured_events) >= 1
        intent_evt = captured_events[0]
        assert intent_evt["intent"] == IntentType.SENSITIVE.value
        assert intent_evt["suggested_lane"] == "local"
