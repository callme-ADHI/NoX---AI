"""Scriptable FakeProvider for deterministic testing without live models."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from noxai.models.base import (
    ChatChunk,
    Message,
    Provider,
    ProviderHealth,
    ToolCall,
    ToolSpec,
)


class PrivacyAssertionError(AssertionError):
    """Raised when a non-local/cloud provider receives private data."""


class FakeProvider(Provider):
    """Mock provider with scriptable responses and privacy assertion checks."""

    def __init__(
        self,
        name: str = "fake",
        is_local: bool = True,
        fail_on_private: bool = True,
    ) -> None:
        self.name = name
        self.is_local = is_local
        self.fail_on_private = fail_on_private

        # Scripted queues
        self.responses: list[list[ChatChunk] | Exception] = []
        self.embed_responses: list[list[list[float]]] = []

        # History tracking
        self.calls: list[dict[str, Any]] = []

    def queue_response(self, chunks: list[ChatChunk] | str | Exception) -> None:
        """Enqueue a scripted response for the next chat() call."""
        if isinstance(chunks, str):
            self.responses.append([ChatChunk(delta_text=chunks, finish_reason="stop")])
        elif isinstance(chunks, list):
            self.responses.append(chunks)
        else:
            self.responses.append(chunks)

    def queue_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        call_id: str = "call_fake1",
    ) -> None:
        """Enqueue a response that produces a tool call."""
        self.responses.append(
            [
                ChatChunk(
                    delta_tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
                    finish_reason="tool_calls",
                )
            ]
        )

    def queue_embed(self, embeddings: list[list[float]]) -> None:
        """Enqueue embeddings for the next embed() call."""
        self.embed_responses.append(embeddings)

    async def chat(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        stream: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        """Record the call, check privacy rules, and yield scripted chunks."""
        # 1. Privacy inspection for cloud/non-local providers
        if not self.is_local and self.fail_on_private:
            for msg in messages:
                if msg.is_private():
                    preview = msg.get_text()[:60]
                    raise PrivacyAssertionError(
                        f"Non-local provider '{self.name}' received PRIVATE message: {preview}..."
                    )

        # 2. Record call
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "tools": tools,
                "stream": stream,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )

        # 3. Retrieve scripted response
        is_extractor = False
        if messages:
            first_txt = str(messages[0].content).lower()
            if (
                "extractor" in first_txt
                or "extract meaningful" in first_txt
                or ("facts" in first_txt and "episode_summary" in first_txt)
            ):
                is_extractor = True

        if is_extractor:
            # Check if the next queued response is explicitly formatted for extractor
            next_is_extractor = False
            if self.responses and isinstance(self.responses[0], list) and self.responses[0]:
                chunk_txt = getattr(self.responses[0][0], "delta_text", None) or ""
                if "facts" in chunk_txt or "episode_summary" in chunk_txt:
                    next_is_extractor = True

            if next_is_extractor:
                resp = self.responses.pop(0)
            else:
                yield ChatChunk(
                    delta_text=(
                        '{"facts": [], "episode_summary": "Turn summary", "importance": 0.5}'
                    ),
                    finish_reason="stop",
                )
                return
        else:
            if not self.responses:
                is_planner = False
                is_worker = False
                is_classifier = False
                last_user_txt = ""
                last_user_idx = -1
                for idx, m in enumerate(messages):
                    c_lower = str(m.content).lower()
                    if "strategic planner" in c_lower or "decompose the goal" in c_lower:
                        is_planner = True
                    elif (
                        "specialized autonomous worker" in c_lower
                        or "execute this subtask" in c_lower
                    ):
                        is_worker = True
                    elif (
                        "query classifier" in c_lower
                        or "routing classifier" in c_lower
                        or "decide if the user request is simple" in c_lower
                    ):
                        is_classifier = True
                    if m.role == "user":
                        last_user_idx = idx
                        last_user_txt = str(m.content).lower()

                if is_classifier:
                    import json

                    yield ChatChunk(
                        delta_text=json.dumps(
                            {
                                "complex": False,
                                "reason": "Evaluated as simple task in test mode",
                            }
                        ),
                        finish_reason="stop",
                    )
                    return

                if is_planner:
                    import json

                    yield ChatChunk(
                        delta_text=json.dumps(
                            {
                                "goal": "Autonomous breakdown",
                                "subtasks": [
                                    {
                                        "id": "t1",
                                        "title": "Gather research sources",
                                        "objective": "Query web feeds and fetch news",
                                        "kind": "fetch",
                                        "depends_on": [],
                                        "suggested_tools": [],
                                        "expected_output": "Fetched sources",
                                    },
                                    {
                                        "id": "t2",
                                        "title": "Analyze findings",
                                        "objective": "Extract facts and summarize",
                                        "kind": "research",
                                        "depends_on": ["t1"],
                                        "suggested_tools": [],
                                        "expected_output": "Filtered report",
                                    },
                                ],
                                "synthesis_instructions": "Synthesize summary report",
                            }
                        ),
                        finish_reason="stop",
                    )
                    return

                if is_worker:
                    import json

                    yield ChatChunk(
                        delta_text=json.dumps(
                            {
                                "findings": (
                                    "Subtask completed successfully with verified citations."
                                ),
                                "verified_sources": ["https://example.com/verified-news"],
                                "data": {},
                            }
                        ),
                        finish_reason="stop",
                    )
                    return

                has_tool_result = (
                    any(m.role == "tool" for m in messages[last_user_idx + 1 :])
                    if last_user_idx >= 0
                    else False
                )
                if not has_tool_result and (
                    "write tool" in last_user_txt
                    or "create file" in last_user_txt
                    or "approval" in last_user_txt
                ):
                    yield ChatChunk(
                        delta_tool_calls=[
                            ToolCall(
                                id="call_write_test1",
                                name="write_file",
                                arguments={
                                    "path": "/tmp/nox-test.txt",  # noqa: S108
                                    "content": "Autonomous NoX test content",
                                },
                            )
                        ],
                        finish_reason="tool_calls",
                    )
                    return

                if has_tool_result:
                    yield ChatChunk(
                        delta_text="Action processed: /tmp/nox-test.txt operation completed.",
                        finish_reason="stop",
                    )
                    return

                # Streaming text fallback
                yield ChatChunk(
                    delta_text="Hello from NoX local model! ",
                    finish_reason=None,
                )
                await asyncio.sleep(0.01)
                yield ChatChunk(
                    delta_text="Everything is working smoothly in test mode.",
                    finish_reason="stop",
                )
                return

            resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp

        for chunk in resp:
            yield chunk
            # Yield control briefly to simulate async streaming
            await asyncio.sleep(0)

    async def embed(
        self,
        *,
        model: str,
        texts: list[str],
    ) -> list[list[float]]:
        """Return scripted embeddings or a default 8-dim vector per text."""
        self.calls.append({"model": model, "texts": texts, "action": "embed"})
        if self.embed_responses:
            return self.embed_responses.pop(0)
        # Default mock vector
        return [[0.1] * 8 for _ in texts]

    async def health(self) -> ProviderHealth:
        """Always return ok for FakeProvider."""
        return ProviderHealth(status="ok", latency_ms=1.0, details={"mock": True})
