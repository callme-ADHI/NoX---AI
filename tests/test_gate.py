"""Tests for complexity gate (Section 6.4)."""

from __future__ import annotations

import pytest

from noxai.agent.gate import ComplexityResult, evaluate_complexity
from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.models.base import ChatChunk
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter


@pytest.fixture
def fake_router() -> tuple[ModelRouter, FakeProvider]:
    fake = FakeProvider()
    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="fake-brain"),
                "embed": ModelRoleConfig(provider="fake", model="fake-embed"),
            }
        )
    )
    router = ModelRouter(config=cfg, providers={"fake": fake})
    return router, fake


@pytest.mark.asyncio
async def test_gate_heuristic_keywords() -> None:
    """Explicit keywords trigger complex classification immediately."""
    for kw in [
        "detailed report on kubernetes",
        "in-depth guide to rust",
        "research cybersecurity in india",
        "compare postgres and sqlite",
        "give me a full breakdown of ai news",
    ]:
        res = await evaluate_complexity(kw)
        assert res.is_complex is True, f"Expected complex for: {kw}"
        assert "keyword" in res.reason.lower()


@pytest.mark.asyncio
async def test_gate_force_deep_prefix() -> None:
    """/deep prefix forces complex and strips the command prefix."""
    res = await evaluate_complexity("/deep tell me a joke")
    assert res.is_complex is True
    assert res.cleaned_message == "tell me a joke"
    assert "/deep" in res.reason


@pytest.mark.asyncio
async def test_gate_simple_short_patterns() -> None:
    """Common greetings and direct single commands are classified simple."""
    for s in ["hi", "hello", "uptime", "date", "whoami", "what time is it?"]:
        res = await evaluate_complexity(s)
        assert res.is_complex is False, f"Expected simple for: {s}"
        assert "direct" in res.reason.lower() or "simple" in res.reason.lower()


@pytest.mark.asyncio
async def test_gate_word_count_threshold() -> None:
    """Messages with > 60 words trigger complex classification."""
    long_msg = "word " * 65
    res = await evaluate_complexity(long_msg)
    assert res.is_complex is True
    assert "word count" in res.reason.lower()


@pytest.mark.asyncio
async def test_gate_multi_question_marks() -> None:
    """Messages containing 3 or more question marks are deemed multi-ask complex."""
    multi_ask = "Where is the file? Who created it? When was it last modified?"
    res = await evaluate_complexity(multi_ask)
    assert res.is_complex is True
    assert "multiple" in res.reason.lower()


@pytest.mark.asyncio
async def test_gate_llm_classification_simple(
    fake_router: tuple[ModelRouter, FakeProvider],
) -> None:
    """Local model call decides simple when heuristics are ambiguous."""
    router, fake = fake_router
    fake.queue_response(
        [ChatChunk(delta_text='{"complex": false, "reason": "single factual lookup"}')]
    )
    res = await evaluate_complexity("explain what a socket is briefly", router=router)
    assert res.is_complex is False
    assert "single factual lookup" in res.reason


@pytest.mark.asyncio
async def test_gate_llm_classification_complex(
    fake_router: tuple[ModelRouter, FakeProvider],
) -> None:
    """Local model call decides complex for multi-faceted topics."""
    router, fake = fake_router
    fake.queue_response(
        [ChatChunk(delta_text='{"complex": true, "reason": "multi-phase investigation"}')]
    )
    res = await evaluate_complexity("investigate the supply chain vulnerability", router=router)
    assert res.is_complex is True
    assert "multi-phase investigation" in res.reason


@pytest.mark.asyncio
async def test_gate_llm_failure_fails_safe(
    fake_router: tuple[ModelRouter, FakeProvider],
) -> None:
    """If local model call throws or produces malformed text, gate falls back safely."""
    router, fake = fake_router
    fake.queue_response([ChatChunk(delta_text="sorry I cannot answer this")])
    res = await evaluate_complexity("something ambiguous here", router=router)
    # Should not raise exception
    assert isinstance(res, ComplexityResult)
