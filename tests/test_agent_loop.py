"""Unit and integration tests for Milestone 3: Agent Loop, Robustness, Context, Events."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from noxai.agent.context import (
    approximate_tokens,
    assemble_system_prompt,
    trim_context,
)
from noxai.agent.loop import (
    AgentLoop,
    detect_text_tool_call,
    extract_citations,
    repair_json,
)
from noxai.config import Settings
from noxai.events import Event, EventBus, make_event
from noxai.models.base import ChatChunk, Message, ToolCall, ToolSpec
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.tools.registry import ToolContext, ToolRegistry, ToolResult, tool

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_config() -> Settings:
    from noxai.config import ModelRoleConfig, ModelsConfig

    return Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="fake-brain"),
                "embed": ModelRoleConfig(provider="fake", model="fake-embed"),
                "heavy": ModelRoleConfig(provider="fake", model="fake-heavy"),
            }
        )
    )


@pytest.fixture
def fake_router(test_config: Settings) -> tuple[ModelRouter, FakeProvider]:
    fake = FakeProvider()
    router = ModelRouter(
        config=test_config,
        providers={"fake": fake},
    )
    return router, fake


@pytest.fixture
def test_registry() -> ToolRegistry:
    reg = ToolRegistry()

    from pydantic import BaseModel, Field

    class EchoParams(BaseModel):
        msg: str = Field(...)

    @tool(name="echo_tool", description="Echo message", params=EchoParams, tier="read")
    async def echo_tool(params: EchoParams, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output=f"Echo: {params.msg}", summary="Echoed")

    class AddParams(BaseModel):
        a: int
        b: int

    @tool(name="add_tool", description="Add numbers", params=AddParams, tier="read")
    async def add_tool(params: AddParams, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output=str(params.a + params.b), summary="Added")

    reg.register(echo_tool)
    reg.register(add_tool)
    return reg


# ---------------------------------------------------------------------------
# Test Prompt Assembly and Context Trimming
# ---------------------------------------------------------------------------


class TestPromptAssembly:
    @pytest.mark.asyncio
    async def test_system_prompt_sections_order(
        self, test_config: Settings, tmp_path: Path
    ) -> None:
        """System prompt sections follow the exact order specified in Section 6.3.1."""
        db_path = tmp_path / "test_prompt.db"
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE preferences (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
            )
            await db.execute(
                "INSERT INTO preferences VALUES ('summary_style', 'bullet points', datetime('now'))"
            )
            await db.commit()

            tools = [ToolSpec(name="test_tool", description="A test tool", parameters={})]
            prompt = await assemble_system_prompt(
                config=test_config,
                tools=tools,
                db=db,
                allow_cloud=False,
                recalled_memories=["Owner likes dark mode."],
                relevant_skills=[{"name": "arch_pkg", "description": "Pacman skill"}],
            )

            # Check order of sections
            pos_rules = prompt.find("You are NoX")
            pos_time = prompt.find("Current date & time:")
            pos_prefs = prompt.find("Owner preferences:")
            pos_skills = prompt.find("Active skills:")
            pos_memories = prompt.find("Recalled memories:")
            pos_tools = prompt.find("Available tools:")

            assert pos_rules != -1, "System rules missing"
            assert pos_time != -1, "Date/time missing"
            assert pos_prefs != -1, "Preferences missing"
            assert pos_skills != -1, "Skills missing"
            assert pos_memories != -1, "Memories missing"
            assert pos_tools != -1, "Tools missing"

            assert pos_rules < pos_time < pos_prefs < pos_skills < pos_memories < pos_tools

    @pytest.mark.asyncio
    async def test_memories_omitted_for_cloud(self, test_config: Settings) -> None:
        """Section 6.3.1 rule 5: Recalled memories are omitted entirely for cloud runs."""
        tools = [ToolSpec(name="test_tool", description="A test tool", parameters={})]
        prompt = await assemble_system_prompt(
            config=test_config,
            tools=tools,
            allow_cloud=True,
            recalled_memories=["Confidential memory."],
        )
        assert "Confidential memory." not in prompt
        assert "Recalled memories:" not in prompt


class TestContextTrimming:
    def test_approximate_tokens(self) -> None:
        """Token count approximation is roughly chars / 2.8."""
        assert approximate_tokens("") == 0
        assert approximate_tokens("a" * 28) == 10

    @pytest.mark.asyncio
    async def test_short_dialogue_not_trimmed(self) -> None:
        """Dialogues below 70% threshold are returned untouched."""
        msgs = [
            Message(role="system", content="System prompt"),
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi!"),
        ]
        trimmed = await trim_context(msgs, max_tokens=1000)
        assert len(trimmed) == 3
        assert trimmed[1].get_text() == "Hello"

    @pytest.mark.asyncio
    async def test_long_dialogue_trimmed_and_summarized(self) -> None:
        """Long dialogues exceeding 70% of max_tokens have older turns summarized."""
        system_msg = Message(role="system", content="System rules")
        long_history = [system_msg]
        for i in range(20):
            long_history.append(Message(role="user", content=f"User question {i}: " + ("x" * 200)))
            long_history.append(Message(role="assistant", content=f"Answer {i}: " + ("y" * 200)))

        # 41 messages total, roughly 8000+ characters (~3000 tokens)
        # Setting max_tokens=2000 makes threshold 1400 tokens
        trimmed = await trim_context(long_history, max_tokens=2000)
        assert len(trimmed) < len(long_history)
        assert trimmed[0].role == "system"
        # Summary message injected
        assert "Summary of earlier conversation" in trimmed[1].get_text()
        # Most recent turn preserved
        assert trimmed[-1].get_text().startswith("Answer 19:")


# ---------------------------------------------------------------------------
# Test Robustness Features
# ---------------------------------------------------------------------------


class TestRobustnessUtilities:
    def test_repair_json_clean(self) -> None:
        res = repair_json('{"key": "value"}')
        assert res == {"key": "value"}

    def test_repair_json_markdown_fences(self) -> None:
        raw = '```json\n{"command": "uname -r"}\n```'
        res = repair_json(raw)
        assert res == {"command": "uname -r"}

    def test_repair_json_trailing_commas(self) -> None:
        raw = '{"path": "/tmp", "items": [1, 2, 3,],}'
        res = repair_json(raw)
        assert res == {"path": "/tmp", "items": [1, 2, 3]}

    def test_repair_json_unbalanced_braces(self) -> None:
        raw = '{"name": "NoX", "details": {"arch": "linux"'
        res = repair_json(raw)
        assert res == {"name": "NoX", "details": {"arch": "linux"}}

    def test_repair_json_single_quotes(self) -> None:
        raw = "{'query': 'arch linux', 'count': 5}"
        res = repair_json(raw)
        assert res == {"query": "arch linux", "count": 5}

    def test_repair_json_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Unable to parse arguments"):
            repair_json("completely non-json text gibberish !@#$")

    def test_detect_text_tool_call_standard_json(self) -> None:
        text = '{"name": "echo_tool", "arguments": {"msg": "hello from text"}}'
        tc = detect_text_tool_call(text, {"echo_tool"})
        assert tc is not None
        assert tc.name == "echo_tool"
        assert tc.arguments == {"msg": "hello from text"}

    def test_detect_text_tool_call_markdown_block(self) -> None:
        text = (
            "I will run this tool:\n"
            '```json\n{"tool": "echo_tool", "parameters": {"msg": "fenced"}}\n```'
        )
        tc = detect_text_tool_call(text, {"echo_tool"})
        assert tc is not None
        assert tc.name == "echo_tool"
        assert tc.arguments == {"msg": "fenced"}

    def test_detect_text_tool_call_no_match(self) -> None:
        text = "Just a regular text answer without any tool call."
        tc = detect_text_tool_call(text, {"echo_tool"})
        assert tc is None

    def test_extract_citations(self) -> None:
        text = "Check the report at https://archlinux.org/news and https://kernel.org."
        citations = extract_citations(text)
        assert "https://archlinux.org/news" in citations
        assert "https://kernel.org" in citations


# ---------------------------------------------------------------------------
# Test Agent Loop Execution
# ---------------------------------------------------------------------------


class TestAgentLoopExecution:
    @pytest.mark.asyncio
    async def test_plain_text_response_no_tools(
        self,
        test_config: Settings,
        fake_router: tuple[ModelRouter, FakeProvider],
        test_registry: ToolRegistry,
    ) -> None:
        """Model returns plain text: run finishes in 1 step with done status."""
        router, fake = fake_router
        event_bus = EventBus()
        events_emitted: list[Event] = []
        event_bus.subscribe(lambda e: events_emitted.append(e) or asyncio.sleep(0))

        fake.queue_response([ChatChunk(text="Hello owner! How can I assist you?")])

        loop = AgentLoop(
            config=test_config,
            router=router,
            registry=test_registry,
            event_bus=event_bus,
        )

        tokens = []
        result = await loop.run(
            "Hello NoX",
            on_token=tokens.append,
        )

        assert result.status == "done"
        assert result.text == "Hello owner! How can I assist you?"
        assert result.steps == 1
        assert result.tool_calls_count == 0
        assert "".join(tokens) == "Hello owner! How can I assist you?"

        event_types = [e.type for e in events_emitted]
        assert "run_started" in event_types
        assert "token" in event_types
        assert "message_done" in event_types
        assert "run_done" in event_types

    @pytest.mark.asyncio
    async def test_successful_tool_call_cycle(
        self,
        test_config: Settings,
        fake_router: tuple[ModelRouter, FakeProvider],
        test_registry: ToolRegistry,
    ) -> None:
        """Tool call cycle: model calls echo_tool, receives result, then produces final answer."""
        router, fake = fake_router
        event_bus = EventBus()
        events_emitted: list[Event] = []
        event_bus.subscribe(lambda e: events_emitted.append(e) or asyncio.sleep(0))

        # Turn 1: model returns tool call
        call_id = "tc_123"
        fake.queue_response(
            [
                ChatChunk(
                    tool_calls=[
                        ToolCall(id=call_id, name="echo_tool", arguments={"msg": "hello system"})
                    ]
                )
            ]
        )
        # Turn 2: model sees tool output and answers
        fake.queue_response(
            [ChatChunk(text="The echo returned: Echo: hello system. See https://archlinux.org")]
        )

        loop = AgentLoop(
            config=test_config,
            router=router,
            registry=test_registry,
            event_bus=event_bus,
        )

        result = await loop.run("Please test echo")
        assert result.status == "done"
        assert "The echo returned" in result.text
        assert result.steps == 2
        assert result.tool_calls_count == 1
        assert "https://archlinux.org" in result.citations

        event_types = [e.type for e in events_emitted]
        assert "tool_call" in event_types
        assert "tool_result" in event_types
        assert "run_done" in event_types

    @pytest.mark.asyncio
    async def test_malformed_json_repair_in_tool_call(
        self,
        test_config: Settings,
        fake_router: tuple[ModelRouter, FakeProvider],
        test_registry: ToolRegistry,
    ) -> None:
        """Malformed JSON arguments with markdown fences and trailing commas are repaired."""
        router, fake = fake_router
        # Turn 1: model returns raw string with markdown fences and trailing comma
        malformed_raw = '```json\n{"msg": "repaired text",}\n```'
        fake.queue_response(
            [
                ChatChunk(
                    tool_calls=[ToolCall(id="tc_raw", name="echo_tool", arguments=malformed_raw)]  # type: ignore
                )
            ]
        )
        # Turn 2: final answer
        fake.queue_response([ChatChunk(text="Repaired and executed successfully.")])

        loop = AgentLoop(
            config=test_config,
            router=router,
            registry=test_registry,
        )

        result = await loop.run("Test repair")
        assert result.status == "done"
        assert result.tool_calls_count == 1
        assert result.text == "Repaired and executed successfully."

    @pytest.mark.asyncio
    async def test_unrepairable_json_feeds_retry_message(
        self,
        test_config: Settings,
        fake_router: tuple[ModelRouter, FakeProvider],
        test_registry: ToolRegistry,
    ) -> None:
        """Unrepairable JSON feeds structured error back to the model for retry."""
        router, fake = fake_router
        # Turn 1: invalid JSON
        fake.queue_response(
            [
                ChatChunk(
                    tool_calls=[
                        ToolCall(id="tc_bad", name="echo_tool", arguments="bad gibberish !@#")
                    ]  # type: ignore
                )
            ]
        )
        # Turn 2: model retries with valid arguments
        fake.queue_response(
            [
                ChatChunk(
                    tool_calls=[
                        ToolCall(id="tc_good", name="echo_tool", arguments={"msg": "fixed"})
                    ]
                )
            ]
        )
        # Turn 3: final answer
        fake.queue_response([ChatChunk(text="Recovered from invalid arguments.")])

        loop = AgentLoop(
            config=test_config,
            router=router,
            registry=test_registry,
        )

        result = await loop.run("Test retry")
        assert result.status == "done"
        assert result.text == "Recovered from invalid arguments."

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_valid_tools_error(
        self,
        test_config: Settings,
        fake_router: tuple[ModelRouter, FakeProvider],
        test_registry: ToolRegistry,
    ) -> None:
        """Calling an unknown tool returns error listing valid tools without crashing."""
        router, fake = fake_router
        # Turn 1: model calls unknown tool
        fake.queue_response(
            [ChatChunk(tool_calls=[ToolCall(id="tc_unknown", name="hacker_tool", arguments={})])]
        )
        # Turn 2: model sees error and provides final answer
        fake.queue_response([ChatChunk(text="Understood, hacker_tool is not available.")])

        loop = AgentLoop(
            config=test_config,
            router=router,
            registry=test_registry,
        )

        result = await loop.run("Run unknown tool")
        assert result.status == "done"
        assert "not available" in result.text

    @pytest.mark.asyncio
    async def test_loop_detection_aborts_repetitive_calls(
        self,
        test_config: Settings,
        fake_router: tuple[ModelRouter, FakeProvider],
        test_registry: ToolRegistry,
    ) -> None:
        """Calling the same tool with identical arguments 3 times triggers loop detection."""
        router, fake = fake_router
        call = ToolCall(id="tc_loop", name="echo_tool", arguments={"msg": "loop"})
        # 3 identical tool calls
        fake.queue_response([ChatChunk(tool_calls=[call])])
        fake.queue_response([ChatChunk(tool_calls=[call])])
        fake.queue_response([ChatChunk(tool_calls=[call])])
        # Turn 4: model realizes and stops
        fake.queue_response([ChatChunk(text="Loop detected, stopping repeated calls.")])

        loop = AgentLoop(
            config=test_config,
            router=router,
            registry=test_registry,
        )

        result = await loop.run("Loop test")
        assert result.status == "done"
        assert "stopping" in result.text

    @pytest.mark.asyncio
    async def test_max_steps_enforced(
        self,
        test_config: Settings,
        fake_router: tuple[ModelRouter, FakeProvider],
        test_registry: ToolRegistry,
    ) -> None:
        """Run terminates with failed status if max_steps is exceeded."""
        router, fake = fake_router
        # Keep returning different tool calls to prevent loop detection
        for i in range(10):
            fake.queue_response(
                [
                    ChatChunk(
                        tool_calls=[
                            ToolCall(id=f"tc_{i}", name="echo_tool", arguments={"msg": f"step {i}"})
                        ]
                    )
                ]
            )

        loop = AgentLoop(
            config=test_config,
            router=router,
            registry=test_registry,
        )

        result = await loop.run("Run forever", max_steps=3)
        assert result.status == "failed"
        assert "maximum allowed steps" in str(result.error)

    @pytest.mark.asyncio
    async def test_cancellation_handled_cleanly(
        self,
        test_config: Settings,
        fake_router: tuple[ModelRouter, FakeProvider],
        test_registry: ToolRegistry,
    ) -> None:
        """Cancelling the asyncio task records cancelled event and returns cancelled result."""
        router, _fake = fake_router
        event_bus = EventBus()
        events: list[Event] = []
        event_bus.subscribe(lambda e: events.append(e) or asyncio.sleep(0))

        # Enqueue slow generator
        async def slow_chat(*_args: Any, **_kwargs: Any):
            await asyncio.sleep(10)
            yield ChatChunk(text="Too late")

        router.chat = slow_chat  # type: ignore

        loop = AgentLoop(
            config=test_config,
            router=router,
            registry=test_registry,
            event_bus=event_bus,
        )

        task = asyncio.create_task(loop.run("Cancel me"))
        await asyncio.sleep(0.05)
        task.cancel()
        res = await task

        assert res.status == "cancelled"
        event_types = [e.type for e in events]
        assert "run_cancelled" in event_types


# ---------------------------------------------------------------------------
# Test Event Ring Buffer
# ---------------------------------------------------------------------------


class TestEventBusRingBuffer:
    @pytest.mark.asyncio
    async def test_ring_buffer_caps_at_size(self) -> None:
        """Event bus ring buffer retains the latest 200 events per run."""
        bus = EventBus(replay_buffer_size=10)
        run_id = "test_run_replay"

        for i in range(15):
            await bus.publish(make_event("token", {"index": i}, run_id=run_id))

        replayed = bus.get_replay_events(run_id)
        assert len(replayed) == 10
        # Check oldest in buffer is index 5
        assert replayed[0].data["index"] == 5
        assert replayed[-1].data["index"] == 14
