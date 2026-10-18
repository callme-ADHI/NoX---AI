"""Tests for Ollama provider: message formatting, error handling, and tool conversion."""

from __future__ import annotations

from noxai.models.base import Message, ToolCall, ToolSpec
from noxai.models.ollama import OllamaProvider
from noxai.taint import Content


class TestOllamaFormatting:
    def test_message_formatting_plain_text(self) -> None:
        provider = OllamaProvider()
        msgs = [
            Message(role="system", content="You are NoX."),
            Message(role="user", content="Hello"),
        ]
        formatted = provider._format_messages(msgs)
        assert len(formatted) == 2
        assert formatted[0] == {"role": "system", "content": "You are NoX."}
        assert formatted[1] == {"role": "user", "content": "Hello"}

    def test_message_formatting_untrusted_wrapped(self) -> None:
        provider = OllamaProvider()
        msgs = [
            Message(
                role="user",
                content=Content.untrusted("malicious instructions", source="web"),
            )
        ]
        formatted = provider._format_messages(msgs)
        assert len(formatted) == 1
        content_text = formatted[0]["content"]
        assert "<untrusted_content" in content_text
        assert "malicious instructions" in content_text

    def test_tool_specification_formatting(self) -> None:
        provider = OllamaProvider()
        tools = [
            ToolSpec(
                name="get_time",
                description="Get current system time",
                parameters={"type": "object", "properties": {}},
            )
        ]
        formatted = provider._format_tools(tools)
        assert formatted is not None
        assert len(formatted) == 1
        assert formatted[0]["type"] == "function"
        assert formatted[0]["function"]["name"] == "get_time"
        assert formatted[0]["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_tool_calls_in_assistant_message(self) -> None:
        provider = OllamaProvider()
        msg = Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="get_time", arguments={"timezone": "UTC"})],
        )
        formatted = provider._format_messages([msg])
        assert len(formatted) == 1
        assert "tool_calls" in formatted[0]
        assert formatted[0]["tool_calls"][0]["function"]["name"] == "get_time"
        assert formatted[0]["tool_calls"][0]["function"]["arguments"] == {"timezone": "UTC"}
