"""Comprehensive test suite for NoX memory subsystem (Milestone 4).

Tests:
1. Fact CRUD and automatic facts_history snapshotting.
2. Hard-delete ('forget it') cascading to history, vectors, and episodes.
3. Episode CRUD and archiving.
4. Vector serialization and NumPy brute-force similarity search.
5. Secret filter detecting credentials, keys, passwords, and cards.
6. Memory extraction, deduplication, and JSON repair.
7. Hybrid recall ranking (cosine, BM25, recency decay, pinned priority, token budget).
8. Nightly consolidation (daily summary, fact merging, archiving, audit log).
9. Memory tools: save_fact, recall_facts, forget_fact, pin_fact.
10. Cross-conversation recall and forget end-to-end integration test.
"""

from __future__ import annotations

import datetime

import aiosqlite
import pytest

from noxai.agent.loop import AgentLoop
from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.db import fetch_one
from noxai.memory.consolidate import run_memory_consolidation
from noxai.memory.embed import (
    bytes_to_vector,
    cosine_similarity,
    search_fact_vectors,
    vector_to_bytes,
)
from noxai.memory.extract import contains_secret, extract_and_store_turn
from noxai.memory.recall import (
    calculate_hybrid_score,
    compute_recency_decay,
    recall_memories,
)
from noxai.memory.store import (
    archive_episode,
    create_episode,
    create_fact,
    get_episode,
    get_fact,
    get_fact_by_key,
    get_fact_history,
    hard_delete_episode,
    hard_delete_fact,
    list_episodes,
    list_facts,
    pin_fact,
    soft_delete_fact,
    update_fact,
)
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.tools.builtin.memory_tools import (
    ForgetFactParams,
    RecallFactsParams,
    SaveFactParams,
    forget_fact,
    recall_facts,
    save_fact,
)
from noxai.tools.registry import ToolContext, ToolRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def memory_db() -> aiosqlite.Connection:
    """In-memory SQLite database initialized with NoX schema."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row

    # Initialize tables directly
    from pathlib import Path

    migration_sql = (
        Path(__file__).parent.parent / "src" / "noxai" / "migrations" / "001_initial.sql"
    ).read_text(encoding="utf-8")

    await conn.executescript(migration_sql)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
def fake_router() -> tuple[ModelRouter, FakeProvider]:
    """Router with FakeProvider for local brain and embed roles."""
    fake = FakeProvider(name="fake", is_local=True)
    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="fake-brain"),
                "embed": ModelRoleConfig(provider="fake", model="fake-embed"),
                "heavy": ModelRoleConfig(provider="fake", model="fake-heavy"),
            }
        )
    )
    router = ModelRouter(config=cfg, providers={"fake": fake})
    return router, fake


# ---------------------------------------------------------------------------
# 1. Fact CRUD and History Tracking
# ---------------------------------------------------------------------------


class TestFactStore:
    """Test Fact persistence, history recording, and deletion semantics."""

    async def test_create_and_get_fact(self, memory_db: aiosqlite.Connection) -> None:
        fact = await create_fact(
            memory_db,
            key="preferred_editor",
            value="neovim",
            category="preference",
            confidence=0.95,
            embedding=[0.1, 0.2, 0.3],
        )
        assert fact.id is not None
        assert fact.key == "preferred_editor"
        assert fact.value == "neovim"
        assert fact.category == "preference"
        assert fact.confidence == 0.95
        expected_emb = [0.10000000149011612, 0.20000000298023224, 0.30000001192092896]
        assert fact.embedding == expected_emb  # float32 precision

        fetched = await get_fact(memory_db, fact.id)
        assert fetched is not None
        assert fetched.value == "neovim"

        by_key = await get_fact_by_key(memory_db, "preferred_editor")
        assert by_key is not None
        assert by_key.id == fact.id

    async def test_update_fact_snapshots_history(self, memory_db: aiosqlite.Connection) -> None:
        fact = await create_fact(
            memory_db,
            key="primary_shell",
            value="bash",
            category="preference",
            confidence=1.0,
        )

        # Update value: should create a historical snapshot
        updated = await update_fact(
            memory_db,
            fact.id,
            value="fish",
            confidence=0.9,
            changed_by="owner",
        )
        assert updated is not None
        assert updated.value == "fish"
        assert updated.confidence == 0.9

        history = await get_fact_history(memory_db, fact.id)
        assert len(history) == 1
        assert history[0].value == "bash"
        assert history[0].confidence == 1.0
        assert history[0].changed_by == "owner"

    async def test_update_fact_without_value_change_does_not_create_history(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        fact = await create_fact(
            memory_db,
            key="os_distro",
            value="arch",
            category="device",
            confidence=0.8,
        )

        # Update only confidence
        await update_fact(memory_db, fact.id, confidence=0.95)
        history = await get_fact_history(memory_db, fact.id)
        assert len(history) == 0

    async def test_pin_and_soft_delete_fact(self, memory_db: aiosqlite.Connection) -> None:
        fact = await create_fact(
            memory_db,
            key="city",
            value="Kochi",
            category="profile",
        )
        assert not fact.pinned

        await pin_fact(memory_db, fact.id, pinned=True)
        f_pinned = await get_fact(memory_db, fact.id)
        assert f_pinned is not None and f_pinned.pinned

        await soft_delete_fact(memory_db, fact.id)
        active = await list_facts(memory_db, include_deleted=False)
        assert not any(f.id == fact.id for f in active)

        all_facts = await list_facts(memory_db, include_deleted=True)
        assert any(f.id == fact.id for f in all_facts)

    async def test_hard_delete_purges_fact_history_vectors_and_episodes(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        """Section 6.6: 'Forget X' performs a hard delete of fact,
        history, embedding, and episode text.
        """
        fact = await create_fact(
            memory_db,
            key="secret_project",
            value="ProjectTitan",
            category="project",
            embedding=[0.5, 0.5, 0.5],
        )

        # Create history snapshot
        await update_fact(memory_db, fact.id, value="ProjectTitanV2")

        # Create an episode mentioning the fact
        ep = await create_episode(
            memory_db,
            summary="Owner discussed ProjectTitan deadlines.",
            embedding=[0.5, 0.5, 0.5],
        )

        # Verify items exist before hard delete
        assert await get_fact(memory_db, fact.id) is not None
        assert len(await get_fact_history(memory_db, fact.id)) == 1
        assert (
            await fetch_one(memory_db, "SELECT * FROM vec_facts WHERE fact_id = ?", (fact.id,))
            is not None
        )
        assert await get_episode(memory_db, ep.id) is not None

        # Execute hard delete
        result = await hard_delete_fact(memory_db, fact.id)
        assert result["status"] == "deleted"
        assert result["history_rows_removed"] == 1
        assert result["episodes_removed"] == 1

        # Verify complete erasure
        assert await get_fact(memory_db, fact.id) is None
        assert len(await get_fact_history(memory_db, fact.id)) == 0
        assert (
            await fetch_one(memory_db, "SELECT * FROM vec_facts WHERE fact_id = ?", (fact.id,))
            is None
        )
        assert await get_episode(memory_db, ep.id) is None


# ---------------------------------------------------------------------------
# 2. Episode Store
# ---------------------------------------------------------------------------


class TestEpisodeStore:
    """Test Episode creation, listing, archiving, and deletion."""

    async def test_create_and_archive_episode(self, memory_db: aiosqlite.Connection) -> None:
        ep = await create_episode(
            memory_db,
            summary="User queried disk usage on root partition.",
            kind="conversation",
            importance=0.4,
            embedding=[0.2, 0.4, 0.6],
        )
        assert ep.id is not None
        assert ep.summary == "User queried disk usage on root partition."
        assert ep.importance == 0.4

        episodes = await list_episodes(memory_db, include_archived=False)
        assert any(e.id == ep.id for e in episodes)

        await archive_episode(memory_db, ep.id)
        active = await list_episodes(memory_db, include_archived=False)
        assert not any(e.id == ep.id for e in active)

        all_eps = await list_episodes(memory_db, include_archived=True)
        assert any(e.id == ep.id for e in all_eps)

    async def test_hard_delete_episode(self, memory_db: aiosqlite.Connection) -> None:
        ep = await create_episode(
            memory_db,
            summary="Temporary test episode",
            embedding=[0.1, 0.1],
        )
        assert await get_episode(memory_db, ep.id) is not None
        await hard_delete_episode(memory_db, ep.id)
        assert await get_episode(memory_db, ep.id) is None


# ---------------------------------------------------------------------------
# 3. Vector Operations and Fallback
# ---------------------------------------------------------------------------


class TestVectorSearch:
    """Test vector serialization, cosine similarity, and NumPy fallback search."""

    def test_vector_serialization_roundtrip(self) -> None:
        original = [0.123, -0.456, 0.789, 0.0]
        blob = vector_to_bytes(original)
        assert isinstance(blob, bytes)
        recovered = bytes_to_vector(blob)
        assert len(recovered) == len(original)
        for o, r in zip(original, recovered, strict=True):
            assert pytest.approx(o, rel=1e-5) == r

    def test_cosine_similarity_edge_cases(self) -> None:
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    async def test_numpy_vector_search_facts_ranking(self, memory_db: aiosqlite.Connection) -> None:
        # Insert 3 facts with distinct vectors
        f1 = await create_fact(memory_db, key="k1", value="v1", embedding=[1.0, 0.0, 0.0])
        f2 = await create_fact(memory_db, key="k2", value="v2", embedding=[0.8, 0.2, 0.0])
        f3 = await create_fact(memory_db, key="k3", value="v3", embedding=[0.0, 1.0, 0.0])

        query = [1.0, 0.0, 0.0]
        results = await search_fact_vectors(memory_db, query, top_k=3)
        assert len(results) == 3

        # f1 should be highest (1.0), then f2 (~0.97), then f3 (0.0)
        assert results[0][0] == f1.id
        assert pytest.approx(results[0][1], abs=1e-4) == 1.0
        assert results[1][0] == f2.id
        assert results[2][0] == f3.id


# ---------------------------------------------------------------------------
# 4. Secret Filter
# ---------------------------------------------------------------------------


class TestSecretFilter:
    """Ensure candidate facts/episodes with secrets are dropped per Section 6.6."""

    def test_contains_secret_detection(self) -> None:
        assert contains_secret("Here is my key: nvapi-abcdef1234567890abcdef1234")
        assert contains_secret("OpenAI token sk-abcdef1234567890abcdef1234")
        assert contains_secret("Authorization: Bearer my_secret_token_value_long_1234")
        assert contains_secret("password = supersecretpassword123")
        assert contains_secret("-----BEGIN RSA PRIVATE KEY-----\nMIIE...")
        assert contains_secret("Credit card is 4111 2222 3333 4444")
        assert contains_secret("SSN 123-45-6789")

    def test_clean_text_not_flagged(self) -> None:
        assert not contains_secret("I prefer fish shell and neovim.")
        assert not contains_secret("User lives in Kochi, Kerala.")
        assert not contains_secret("Project NoX is an Arch Linux AI assistant.")


# ---------------------------------------------------------------------------
# 5. Background Extraction and Deduplication
# ---------------------------------------------------------------------------


class TestMemoryExtraction:
    """Test extracting facts and episodes from conversation turns."""

    async def test_extract_and_store_turn_creates_facts_and_episode(
        self,
        memory_db: aiosqlite.Connection,
        fake_router: tuple[ModelRouter, FakeProvider],
    ) -> None:
        router, fake = fake_router

        # Script extractor response
        extractor_json = """
        {
          "facts": [
            {
              "key": "preferred_shell",
              "value": "fish",
              "category": "preference",
              "confidence": 1.0
            }
          ],
          "episode_summary": "Owner stated preference for fish shell.",
          "importance": 0.7
        }
        """
        fake.queue_response(extractor_json)

        res = await extract_and_store_turn(
            user_text="I prefer using fish shell on Arch.",
            assistant_text="I will remember that you prefer fish shell.",
            router=router,
            db=memory_db,
            task_id="run_123",
        )

        assert len(res.facts_created) == 1
        assert res.facts_created[0].key == "preferred_shell"
        assert res.facts_created[0].value == "fish"
        assert res.episode is not None
        assert "fish shell" in res.episode.summary

        # Verify in DB
        fact_in_db = await get_fact_by_key(memory_db, "preferred_shell")
        assert fact_in_db is not None
        assert fact_in_db.value == "fish"

    async def test_extract_and_store_turn_deduplication_updates_fact(
        self,
        memory_db: aiosqlite.Connection,
        fake_router: tuple[ModelRouter, FakeProvider],
    ) -> None:
        router, fake = fake_router

        # Pre-seed fact
        await create_fact(
            memory_db,
            key="default_browser",
            value="firefox",
            category="preference",
        )

        # Extractor emits changed value for same key
        fake.queue_response(
            """
            {
              "facts": [
                {
                  "key": "default_browser",
                  "value": "chromium",
                  "category": "preference",
                  "confidence": 0.9
                }
              ],
              "episode_summary": "Owner switched browser to chromium.",
              "importance": 0.5
            }
            """
        )

        res = await extract_and_store_turn(
            user_text="I switched to chromium.",
            assistant_text="Noted.",
            router=router,
            db=memory_db,
        )

        assert len(res.facts_updated) == 1
        assert res.facts_updated[0].value == "chromium"

        # Verify history was recorded
        history = await get_fact_history(memory_db, res.facts_updated[0].id)
        assert len(history) == 1
        assert history[0].value == "firefox"

    async def test_extract_and_store_turn_drops_secrets(
        self,
        memory_db: aiosqlite.Connection,
        fake_router: tuple[ModelRouter, FakeProvider],
    ) -> None:
        router, fake = fake_router

        fake.queue_response(
            """
            {
              "facts": [
                {
                  "key": "my_api_key",
                  "value": "nvapi-secret1234567890abcdef1234",
                  "category": "other",
                  "confidence": 1.0
                },
                {
                  "key": "safe_fact",
                  "value": "clean data",
                  "category": "other",
                  "confidence": 1.0
                }
              ],
              "episode_summary": "Clean summary",
              "importance": 0.3
            }
            """
        )

        res = await extract_and_store_turn(
            user_text="Save my key nvapi-secret1234567890abcdef1234",
            assistant_text="Done.",
            router=router,
            db=memory_db,
        )

        assert res.dropped_secrets_count == 1
        assert len(res.facts_created) == 1
        assert res.facts_created[0].key == "safe_fact"
        assert await get_fact_by_key(memory_db, "my_api_key") is None


# ---------------------------------------------------------------------------
# 6. Hybrid Recall Ranking
# ---------------------------------------------------------------------------


class TestHybridRecall:
    """Test hybrid scoring formula and ranking behavior."""

    def test_hybrid_score_weights(self) -> None:
        # score = 0.55*cosine + 0.20*bm25 + 0.15*recency + 0.10*importance
        score = calculate_hybrid_score(
            cosine=1.0,
            bm25_norm=1.0,
            recency=1.0,
            importance_or_use=1.0,
        )
        assert pytest.approx(score, abs=1e-4) == 1.0

        score_partial = calculate_hybrid_score(
            cosine=1.0,
            bm25_norm=0.0,
            recency=0.0,
            importance_or_use=0.0,
        )
        assert pytest.approx(score_partial, abs=1e-4) == 0.55

    def test_recency_decay(self) -> None:
        now_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
        assert pytest.approx(compute_recency_decay(now_str, half_life_days=30.0), abs=1e-2) == 1.0

        # Pinned has no decay
        assert compute_recency_decay("2020-01-01 00:00:00", is_pinned=True) == 1.0

    async def test_pinned_facts_rank_first(
        self,
        memory_db: aiosqlite.Connection,
        fake_router: tuple[ModelRouter, FakeProvider],
    ) -> None:
        router, fake = fake_router

        # Script mock embeddings: f1 has low similarity, f2 has high similarity
        # But f1 is pinned!
        fake.embed_responses = [
            [[1.0, 0.0]],  # query embedding
            [[0.0, 1.0]],  # f1 embedding (orthogonal)
            [[1.0, 0.0]],  # f2 embedding (identical)
        ]

        f1 = await create_fact(
            memory_db,
            key="pinned_rule",
            value="Always verify inputs",
            pinned=True,
            embedding=[0.0, 1.0],
        )
        await create_fact(
            memory_db,
            key="normal_fact",
            value="Some general knowledge",
            pinned=False,
            embedding=[1.0, 0.0],
        )

        res = await recall_memories(
            "verify",
            db=memory_db,
            router=router,
            top_k=5,
        )

        assert len(res.facts) >= 2
        # Pinned fact must rank first
        assert res.facts[0].id == f1.id
        assert "Pinned facts" in res.formatted_block


# ---------------------------------------------------------------------------
# 7. Consolidation Routine
# ---------------------------------------------------------------------------


class TestConsolidation:
    """Test nightly memory consolidation."""

    async def test_consolidation_merges_facts_and_archives(
        self,
        memory_db: aiosqlite.Connection,
        fake_router: tuple[ModelRouter, FakeProvider],
    ) -> None:
        router, fake = fake_router

        # 1. Create duplicate facts
        await create_fact(memory_db, key="dup_key", value="Value 1", confidence=0.8)
        await create_fact(memory_db, key="dup_key", value="Value 1 updated", confidence=0.95)

        # 2. Create multiple episodes from today to trigger daily summary
        await create_episode(memory_db, summary="Episode 1 from today")
        await create_episode(memory_db, summary="Episode 2 from today")

        fake.queue_response("Today the owner worked on episode 1 and episode 2.")

        report = await run_memory_consolidation(memory_db, router)

        assert report.facts_merged >= 1
        assert report.episodes_summarized == 2
        assert report.daily_summary_id is not None

        # Verify lower confidence duplicate is soft-deleted
        active = await list_facts(memory_db, include_deleted=False)
        assert len([f for f in active if f.key == "dup_key"]) == 1

        # Verify audit log entry
        audit_row = await fetch_one(
            memory_db, "SELECT * FROM audit_log WHERE tool = 'consolidation'"
        )
        assert audit_row is not None


# ---------------------------------------------------------------------------
# 8. Memory Tools
# ---------------------------------------------------------------------------


class TestMemoryTools:
    """Test save_fact, recall_facts, forget_fact, and pin_fact tools."""

    async def test_tool_save_and_recall_fact(
        self,
        memory_db: aiosqlite.Connection,
        fake_router: tuple[ModelRouter, FakeProvider],
    ) -> None:
        router, _ = fake_router
        cfg = router.config

        ctx = ToolContext(
            run_id="run_tool_1",
            config=cfg,
            db=memory_db,
        )

        # 1. Save fact
        save_params = SaveFactParams(
            key="owner_major",
            value="Computer Science and Cybersecurity",
            category="profile",
        )
        res = await save_fact(save_params, ctx)
        assert res.ok
        assert "Remembered fact" in res.output

        # 2. Recall fact
        recall_params = RecallFactsParams(query="Cybersecurity")
        rec_res = await recall_facts(recall_params, ctx)
        assert rec_res.ok
        assert "owner_major" in rec_res.output

    async def test_tool_save_fact_refuses_secrets(
        self,
        memory_db: aiosqlite.Connection,
        fake_router: tuple[ModelRouter, FakeProvider],
    ) -> None:
        router, _ = fake_router
        ctx = ToolContext(
            run_id="run_tool_2",
            config=router.config,
            db=memory_db,
        )

        params = SaveFactParams(
            key="api_token",
            value="nvapi-1234567890abcdef12345678",
        )
        res = await save_fact(params, ctx)
        assert not res.ok
        assert "Refused" in res.summary

    async def test_tool_forget_fact_hard_delete(
        self,
        memory_db: aiosqlite.Connection,
        fake_router: tuple[ModelRouter, FakeProvider],
    ) -> None:
        router, _ = fake_router
        ctx = ToolContext(
            run_id="run_tool_3",
            config=router.config,
            db=memory_db,
        )

        # Save fact first
        await save_fact(SaveFactParams(key="temp_key", value="to be deleted"), ctx)
        assert await get_fact_by_key(memory_db, "temp_key") is not None

        # Forget fact
        forget_res = await forget_fact(ForgetFactParams(key="temp_key"), ctx)
        assert forget_res.ok
        assert "Permanently forgot" in forget_res.output

        # Fact must no longer exist anywhere in DB
        assert await get_fact_by_key(memory_db, "temp_key") is None


# ---------------------------------------------------------------------------
# 9. Cross-Conversation Acceptance Test (Section 14 M4)
# ---------------------------------------------------------------------------


class TestCrossConversationAcceptance:
    """Done when: telling NoX a fact in one conversation lets it recall that fact

    in a new conversation, and 'forget it' truly removes it.
    """

    async def test_cross_conversation_memory_and_forget(
        self,
        memory_db: aiosqlite.Connection,
        fake_router: tuple[ModelRouter, FakeProvider],
    ) -> None:
        router, fake = fake_router
        registry = ToolRegistry()
        registry.auto_discover()

        loop = AgentLoop(
            router=router,
            registry=registry,
            config=router.config,
        )

        # --- Conversation A: Tell NoX a preference fact ---
        # Model invokes save_fact tool
        fake.queue_tool_call(
            "save_fact",
            {
                "key": "preferred_summaries",
                "value": "short bullet points only",
                "category": "preference",
            },
        )
        fake.queue_response("I have saved your preference for short bullet points.")

        res_a = await loop.run(
            user_message="Remember that I prefer short bullet points for summaries.",
            conversation_id="conv_a",
            db=memory_db,
        )
        assert res_a.status == "done"
        assert res_a.tool_calls_count == 1

        # Verify fact exists in memory store
        saved_fact = await get_fact_by_key(memory_db, "preferred_summaries")
        assert saved_fact is not None
        assert "short bullet points" in saved_fact.value

        # --- Conversation B: New conversation recalls the fact ---
        # When loop.run runs, it calls recall_memories which injects the fact
        # into the system prompt!
        fake.queue_response(
            "Based on your saved preference, here are bullet points:\n- Point 1\n- Point 2"
        )

        res_b = await loop.run(
            user_message="Summarize the latest Linux kernel news for me.",
            conversation_id="conv_b",
            db=memory_db,
        )
        assert res_b.status == "done"
        assert "Point 1" in res_b.text

        # Verify use count of the recalled fact increased
        refetched_fact = await get_fact_by_key(memory_db, "preferred_summaries")
        assert refetched_fact is not None
        assert refetched_fact.use_count >= 1

        # --- Conversation C: Forget the fact ---
        fake.queue_tool_call(
            "forget_fact",
            {"key": "preferred_summaries"},
        )
        fake.queue_response("I have permanently forgotten your summary preference.")

        res_c = await loop.run(
            user_message="Forget my summary preference.",
            conversation_id="conv_c",
            db=memory_db,
        )
        assert res_c.status == "done"
        assert res_c.tool_calls_count == 1

        # Verify truly and completely deleted
        assert await get_fact_by_key(memory_db, "preferred_summaries") is None
        assert (
            await fetch_one(
                memory_db,
                "SELECT * FROM facts WHERE key = 'preferred_summaries'",
            )
            is None
        )
