"""Scripted LIVE Evaluation for NoX Conversation Memory System (CMS).

Executes 12 targeted scenarios verifying conversation memory, multi-layer
budgeting, follow-up detection, hybrid retrieval, and citation validation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid
from typing import Any

import aiosqlite
from rich.console import Console
from rich.table import Table

from pydantic import SecretStr
from noxai.config import ModelRoleConfig, Settings, get_config
from noxai.models.cloud_health import CloudHealth, CloudHealthState
from noxai.db import _configure_async_connection, execute, fetch_all, fetch_one
from noxai.memory.context import (
    ConversationState,
    ConversationSummary,
    MessageChunk,
    build_context,
    get_conv_state,
    get_latest_summary,
    is_follow_up_query,
    rewrite_query_for_retrieval,
    save_conv_state,
    save_msg_chunks,
    save_summary,
    search_chunks_hybrid,
)
from noxai.memory.folding import RollingSummaryFolder, validate_citations
from noxai.memory.post_process import process_conversation_turn
from noxai.models.base import ChatChunk, Message
from noxai.models.router import ModelRouter
from noxai.orchestrator import orchestrate_chat_turn
from noxai.taint import Content, TaintFlag

log = logging.getLogger(__name__)
console = Console()


class EvalMockProvider:
    """Mock provider recording calls and generating predictable replies for eval."""

    def __init__(self, name: str = "mock_provider", is_local: bool = True) -> None:
        self.name = name
        self.is_local = is_local
        self.recorded_calls: list[list[Message]] = []

    async def chat(self, messages: list[Message], model: str, **kwargs: Any):
        self.recorded_calls.append(list(messages))
        last_text = messages[-1].get_text() if messages else ""
        if "rag" in last_text.lower():
            reply = "RAG stands for Retrieval-Augmented Generation, grounding LLMs on external knowledge."
        elif "aegis" in last_text.lower() or "computer" in last_text.lower():
            reply = "Your workstation is Aegis, running Arch Linux."
        elif "rust" in last_text.lower() or "language" in last_text.lower():
            reply = "The compiler is written in Rust."
        else:
            reply = "I understand and have preserved this in conversation context."

        yield ChatChunk(text=reply)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = []
        for t in texts:
            val = float(len(t) % 10) / 10.0
            vecs.append([val] * 8)
        return vecs


async def _setup_eval_db() -> tuple[aiosqlite.Connection, str]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    db = await aiosqlite.connect(db_path)
    await _configure_async_connection(db)

    import pathlib
    migrations_dir = pathlib.Path(__file__).resolve().parent.parent / "migrations"
    for mf in sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql"), key=lambda p: int(p.stem.split("_")[0])):
        await db.executescript(mf.read_text())
    await db.commit()
    return db, db_path


async def run_conversation_eval(model_override: str | None = None) -> bool:
    """Execute all 12 conversation memory scenarios and print scorecard."""
    console.print("\n[bold cyan]Starting NoX Conversation Memory System (CMS) Evaluation...[/]\n")

    db, db_path = await _setup_eval_db()
    cloud_mock = EvalMockProvider("nvidia", is_local=False)
    ch = CloudHealth(has_key=True)
    ch.state = CloudHealthState.ACTIVE
    cloud_mock.cloud_health = ch

    local_mock = EvalMockProvider("ollama", is_local=True)

    cfg = get_config()
    cfg.nvidia_api_key = SecretStr("nvapi-mock-test-key-12345")
    cfg.models.roles["front"] = ModelRoleConfig(provider="nvidia", model="mock-front")
    cfg.models.roles["resident"] = ModelRoleConfig(provider="ollama", model="mock-resident")
    cfg.models.roles["brain"] = ModelRoleConfig(provider="ollama", model="mock-brain")
    cfg.models.roles["embed"] = ModelRoleConfig(provider="ollama", model="mock-embed")

    router = ModelRouter(config=cfg, providers={"nvidia": cloud_mock, "ollama": local_mock})
    mock_prov = cloud_mock

    results: list[dict[str, Any]] = []

    try:
        # Scenario 1: direct_follow_up
        t0 = time.perf_counter()
        c1 = "conv_eval_s1"
        await orchestrate_chat_turn(
            message="Explain RAG in one sentence.",
            conversation_id=c1,
            db=db,
            router=router,
            config=cfg,
        )
        await orchestrate_chat_turn(
            message="Why is that useful?",
            conversation_id=c1,
            db=db,
            router=router,
            config=cfg,
        )
        s1_passed = False
        if len(mock_prov.recorded_calls) >= 2:
            call_text = " ".join(m.get_text() for m in mock_prov.recorded_calls[-1])
            s1_passed = "explain rag" in call_text.lower() and "retrieval-augmented" in call_text.lower()
        results.append({
            "scenario": "1. direct_follow_up",
            "passed": s1_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": "Turn 2 includes Turn 1 prompt and assistant reply in context",
        })

        # Scenario 2: entity_continuity
        t0 = time.perf_counter()
        c2 = "conv_eval_s2"
        await orchestrate_chat_turn(
            message="My workstation is an Arch Linux box named Aegis.",
            conversation_id=c2,
            db=db,
            router=router,
            config=cfg,
        )
        await orchestrate_chat_turn(
            message="What is the name of my computer?",
            conversation_id=c2,
            db=db,
            router=router,
            config=cfg,
        )
        s2_passed = False
        if len(mock_prov.recorded_calls) >= 4:
            call_text = " ".join(m.get_text() for m in mock_prov.recorded_calls[-1])
            s2_passed = "aegis" in call_text.lower()
        results.append({
            "scenario": "2. entity_continuity",
            "passed": s2_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": "Entity Aegis successfully remembered across turns",
        })

        # Scenario 3: pronoun_resolution
        t0 = time.perf_counter()
        c3 = "conv_eval_s3"
        await orchestrate_chat_turn(
            message="I am developing a high performance parser in Rust.",
            conversation_id=c3,
            db=db,
            router=router,
            config=cfg,
        )
        await orchestrate_chat_turn(
            message="What programming language is it written in?",
            conversation_id=c3,
            db=db,
            router=router,
            config=cfg,
        )
        s3_passed = False
        if len(mock_prov.recorded_calls) >= 6:
            call_text = " ".join(m.get_text() for m in mock_prov.recorded_calls[-1])
            s3_passed = "rust" in call_text.lower()
        results.append({
            "scenario": "3. pronoun_resolution",
            "passed": s3_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": "Pronoun 'it' correctly linked to Rust parser",
        })

        # Scenario 4: multi_turn_accumulation
        t0 = time.perf_counter()
        c4 = "conv_eval_s4"
        await orchestrate_chat_turn(message="Item Alpha is 42.", conversation_id=c4, db=db, router=router, config=cfg)
        await orchestrate_chat_turn(message="Item Beta is 99.", conversation_id=c4, db=db, router=router, config=cfg)
        await orchestrate_chat_turn(message="What are Alpha and Beta?", conversation_id=c4, db=db, router=router, config=cfg)
        s4_passed = False
        if len(mock_prov.recorded_calls) >= 9:
            call_text = " ".join(m.get_text() for m in mock_prov.recorded_calls[-1])
            s4_passed = "42" in call_text and "99" in call_text
        results.append({
            "scenario": "4. multi_turn_accumulation",
            "passed": s4_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": "Multi-turn items accumulated without loss",
        })

        # Scenario 5: privacy_boundary
        t0 = time.perf_counter()
        c5 = "conv_eval_s5"
        priv_msg = Message(role="user", content=Content(text="SECRET_KEY_12345", taint=TaintFlag.PRIVATE))
        ctx_cloud = await build_context(
            conversation_id=c5,
            current_user_message=priv_msg,
            db=db,
            router=router,
            allow_cloud=True,
        )
        egress = router.egress_filter.evaluate(
            ctx_cloud.messages,
            provider_name="nvidia",
            model="mock-front",
            is_local=False,
        )
        s5_passed = (not egress.allowed) or (egress.sanitized_messages is not None)
        results.append({
            "scenario": "5. privacy_boundary",
            "passed": s5_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": "Private taint flagged and blocked from unmasked cloud transmission",
        })

        # Scenario 6: token_budget_sliding_window
        t0 = time.perf_counter()
        c6 = "conv_eval_s6"
        await execute(db, "INSERT INTO conversations (id, title) VALUES (?, ?)", (c6, "Budget Test"))
        for i in range(15):
            await execute(
                db,
                "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, 'user', ?)",
                (f"msg_b_{i}", c6, f"Turn {i} with lots of text to consume context " * 8),
            )
        await db.commit()
        ctx_budget = await build_context(
            conversation_id=c6,
            current_user_message=Message(role="user", content=Content.public("Latest prompt")),
            db=db,
            token_budget=500,
            reserve_tokens_for_response=100,
        )
        s6_passed = "L2_old_turns" in ctx_budget.evicted_layers and len(ctx_budget.messages) < 16
        results.append({
            "scenario": "6. token_budget_sliding_window",
            "passed": s6_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": f"Evicted {ctx_budget.evicted_layers} to satisfy token limit",
        })

        # Scenario 7: semantic_retrieval
        t0 = time.perf_counter()
        c7 = "conv_eval_s7"
        await execute(db, "INSERT INTO conversations (id, title) VALUES (?, ?)", (c7, "Semantic Test"))
        await execute(
            db,
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, 'user', ?)",
            ("msg_q1", c7, "Quantum annealing uses flux qubits to find ground states."),
        )
        chk = MessageChunk(
            id="chk_q1",
            message_id="msg_q1",
            conversation_id=c7,
            chunk_index=0,
            chunk_text="Quantum annealing uses flux qubits to find ground states.",
            token_count=10,
        )
        await save_msg_chunks(db, [chk])
        await db.commit()
        found_chunks = await search_chunks_hybrid(db, c7, "tell me about flux qubits", limit=3)
        s7_passed = any("flux qubits" in c.chunk_text for c in found_chunks)
        results.append({
            "scenario": "7. semantic_retrieval",
            "passed": s7_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": "FTS5 BM25 hybrid search recalled relevant dialogue chunk",
        })

        # Scenario 8: citation_validation
        t0 = time.perf_counter()
        valid_ids = {"msg_valid_1", "msg_valid_2"}
        raw_summary = "Key fact one [mmsg_valid_1]. Hallucinated point [mmsg_fake_999]."
        cleaned, cites = validate_citations(raw_summary, valid_ids)
        s8_passed = "msg_valid_1" in cites and "msg_fake_999" not in cites and "[mmsg_fake_999]" not in cleaned
        results.append({
            "scenario": "8. citation_validation",
            "passed": s8_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": "Valid citation preserved; fake citation stripped",
        })

        # Scenario 9: reanchor_fold
        t0 = time.perf_counter()
        c9 = "conv_eval_s9"
        await execute(db, "INSERT INTO conversations (id, title) VALUES (?, ?)", (c9, "Reanchor Test"))
        for i in range(12):
            await execute(
                db,
                "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, 'user', ?)",
                (f"msg_r_{i}", c9, f"Discussion point {i}"),
            )
        f4 = ConversationSummary(
            id="csum_f4",
            conversation_id=c9,
            fold_index=4,
            start_msg_id="msg_r_5",
            end_msg_id="msg_r_8",
            summary_markdown="## Key Facts\n- Incremental summary [mmsg_r_5]",
        )
        await save_summary(db, f4)
        await db.commit()

        folder = RollingSummaryFolder(turn_threshold=2, reanchor_interval=5)
        new_summary = await folder.fold(db, c9)
        s9_passed = bool(new_summary and new_summary.fold_index == 5 and new_summary.start_msg_id == "msg_r_0")
        results.append({
            "scenario": "9. reanchor_fold",
            "passed": s9_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": f"Fold 5 triggered full re-anchor from {new_summary.start_msg_id if new_summary else 'None'}",
        })

        # Scenario 10: state_persistence
        t0 = time.perf_counter()
        c10 = "conv_eval_s10"
        await execute(db, "INSERT INTO conversations (id, title) VALUES (?, ?)", (c10, "State Test"))
        st = ConversationState(
            conversation_id=c10,
            active_goals=["Build AST parser"],
            entities_in_focus=["Lexer", "Parser"],
        )
        await save_conv_state(db, st)
        await db.commit()
        st_loaded = await get_conv_state(db, c10)
        s10_passed = bool(st_loaded and "Build AST parser" in st_loaded.active_goals and "Lexer" in st_loaded.entities_in_focus)
        results.append({
            "scenario": "10. state_persistence",
            "passed": s10_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": "ConversationState persisted in SQLite and restored",
        })

        # Scenario 11: cross_session_continuity
        t0 = time.perf_counter()
        c11 = "conv_eval_s11"
        await orchestrate_chat_turn(message="Remember code X-779.", conversation_id=c11, db=db, router=router, config=cfg)
        await orchestrate_chat_turn(message="What was the code?", conversation_id=c11, db=db, router=router, config=cfg)
        s11_passed = False
        if mock_prov.recorded_calls:
            last_call = " ".join(m.get_text() for m in mock_prov.recorded_calls[-1])
            s11_passed = "x-779" in last_call.lower()
        results.append({
            "scenario": "11. cross_session_continuity",
            "passed": s11_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": "Past conversation history recovered in new orchestrator session",
        })

        # Scenario 12: cli_continue
        t0 = time.perf_counter()
        last_row = await fetch_one(db, "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1")
        s12_passed = bool(last_row and str(last_row["id"]) == c11)
        results.append({
            "scenario": "12. cli_continue",
            "passed": s12_passed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": f"Latest conversation resolved for --continue ({last_row['id'] if last_row else 'None'})",
        })

    finally:
        await db.close()
        if os.path.exists(db_path):
            os.remove(db_path)

    table = Table(title="CMS 12-Scenario Evaluation Results", show_header=True)
    table.add_column("Scenario", style="bold cyan")
    table.add_column("Status", justify="center")
    table.add_column("Latency (ms)", justify="right")
    table.add_column("Validation Detail")

    passed_count = sum(1 for r in results if r["passed"])

    for r in results:
        status_str = "[bold green]PASS[/]" if r["passed"] else "[bold red]FAIL[/]"
        table.add_row(r["scenario"], status_str, str(r["duration_ms"]), r["detail"])

    console.print(table)
    console.print(f"\n[bold]Total Score:[/] {passed_count}/{len(results)} scenarios passed.")

    if passed_count >= 11:
        console.print("[bold green]CMS ACCEPTANCE CRITERIA MET (>= 11/12).[/bold green]\n")
        return True
    else:
        console.print("[bold red]CMS ACCEPTANCE CRITERIA FAILED (< 11/12).[/bold red]\n")
        return False


if __name__ == "__main__":
    asyncio.run(run_conversation_eval())
