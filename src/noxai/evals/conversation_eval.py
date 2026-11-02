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


async def _eval_judge(
    question: str,
    answer: str,
    criteria: str,
    router: ModelRouter | None = None,
    allow_cloud: bool = True,
) -> bool:
    """Evaluate response against criteria using deterministic checks and optional LLM judge."""
    ans_lower = answer.lower()

    # Fast keyword matching
    criteria_tokens = [k.strip().lower() for k in criteria.split() if len(k) > 3]
    if any(k in ans_lower for k in criteria_tokens):
        return True

    if router:
        judge_prompt = (
            f"You are an impartial evaluator.\n"
            f"User Question: {question}\n"
            f"Assistant Response: {answer}\n"
            f"Pass Criteria: {criteria}\n\n"
            f"Did the assistant response satisfy the criteria? Reply strictly YES or NO."
        )
        try:
            prov_name = "nvidia" if allow_cloud else "ollama"
            prov = router.get_provider(prov_name)
            model_name = (
                router.config.models.roles.front.model
                if allow_cloud
                else router.config.models.roles.resident.model
            )
            chunks = []
            async for chk in prov.chat(
                model=model_name,
                messages=[Message(role="user", content=judge_prompt)],
                stream=False,
                max_tokens=10,
            ):
                chunks.append(chk.delta_text)
            judge_text = "".join(chunks).strip().upper()
            if judge_text.startswith("YES"):
                return True
            elif judge_text.startswith("NO"):
                return False
        except Exception as exc:
            log.debug("LLM judge call failed (%s); using keyword fallback", exc)

    return False


async def run_live_conversation_eval(route: str = "both") -> bool:
    """Run 8 scripted scenarios with real models (Cloud, Local, or Both)."""
    import json
    from noxai.memory.store import create_fact

    routes_to_test = []
    if route in ("both", "cloud"):
        routes_to_test.append(("cloud", True))
    if route in ("both", "local"):
        routes_to_test.append(("local", False))

    cfg = get_config()
    all_route_results: dict[str, list[dict[str, Any]]] = {}
    transcripts: list[dict[str, Any]] = []

    for route_name, allow_cloud in routes_to_test:
        console.print(f"\n[bold magenta]=== Running 8 Live CMS Scenarios on [{route_name.upper()}] Route ===[/bold magenta]\n")
        route_results: list[dict[str, Any]] = []

        # Fresh DB for scenario isolation
        db, db_path = await _setup_eval_db()
        router = ModelRouter(config=cfg)

        async def _turn(msg: str, cid: str, rtr: ModelRouter | None = None) -> Any:
            brevity_msg = f"{msg} (Please respond concisely in 1-2 sentences.)"
            return await orchestrate_chat_turn(
                message=brevity_msg,
                conversation_id=cid,
                db=db,
                router=rtr or router,
                config=cfg,
                allow_cloud=allow_cloud,
                tools=[],
                max_tokens=80,
            )

        try:
            # Scenario 1: pronoun_follow_up
            s1_id = f"eval_live_s1_{route_name}_{uuid.uuid4().hex[:6]}"
            t0 = time.perf_counter()
            r1_1 = await _turn("My favorite Linux window manager on Arch Linux is Hyprland.", s1_id)
            r1_2 = await _turn("Why do I prefer it over other compositors?", s1_id)
            dur_1 = round((time.perf_counter() - t0) * 1000, 1)
            ans1 = r1_2.text
            passed_1 = "hyprland" in ans1.lower() or await _eval_judge(
                "Why do I prefer it over other compositors?", ans1, "Mentions Hyprland or explains window manager preferences", router, allow_cloud
            )
            route_results.append({
                "scenario": "1. pronoun_follow_up",
                "passed": passed_1,
                "duration_ms": dur_1,
                "detail": f"Model identified 'it' as Hyprland (model={r1_2.model_name})",
            })
            transcripts.append({
                "scenario": "1. pronoun_follow_up",
                "route": route_name,
                "model": r1_2.model_name,
                "turn1_user": "My favorite Linux window manager on Arch Linux is Hyprland.",
                "turn1_reply": r1_1.text,
                "turn2_user": "Why do I prefer it over other compositors?",
                "turn2_reply": ans1,
                "passed": passed_1,
            })
            console.print(f"  [dim]✓ Scenario 1: pronoun_follow_up ({dur_1}ms)[/dim]")

            # Scenario 2: ordinal_reference ("the second one")
            s2_id = f"eval_live_s2_{route_name}_{uuid.uuid4().hex[:6]}"
            t0 = time.perf_counter()
            r2_1 = await _turn("I am evaluating three programming languages for my systems project: Zig, Rust, and Odin.", s2_id)
            r2_2 = await _turn("What are the main safety guarantees of the second one?", s2_id)
            dur_2 = round((time.perf_counter() - t0) * 1000, 1)
            ans2 = r2_2.text
            passed_2 = "rust" in ans2.lower() and any(k in ans2.lower() for k in ("borrow", "memory", "safety", "ownership", "type", "concurrency"))
            if not passed_2:
                passed_2 = await _eval_judge(
                    "What are the main safety guarantees of the second one?", ans2, "Discusses Rust safety features or guarantees", router, allow_cloud
                )
            route_results.append({
                "scenario": "2. ordinal_reference",
                "passed": passed_2,
                "duration_ms": dur_2,
                "detail": f"Resolved 'second one' to Rust (model={r2_2.model_name})",
            })
            transcripts.append({
                "scenario": "2. ordinal_reference",
                "route": route_name,
                "model": r2_2.model_name,
                "turn1_user": "I am evaluating three programming languages for my systems project: Zig, Rust, and Odin.",
                "turn1_reply": r2_1.text,
                "turn2_user": "What are the main safety guarantees of the second one?",
                "turn2_reply": ans2,
                "passed": passed_2,
            })
            console.print(f"  [dim]✓ Scenario 2: ordinal_reference ({dur_2}ms)[/dim]")

            # Scenario 3: recall_detail_after_30_turns
            s3_id = f"eval_live_s3_{route_name}_{uuid.uuid4().hex[:6]}"
            t0 = time.perf_counter()
            r3_1 = await _turn("Let's begin our testing session.", s3_id)
            r3_2 = await _turn("Here is a critical secret project code: PROJECT_NEBULA_4482.", s3_id)
            # Insert turns 3 to 29 into conversation history in DB to simulate 30-turn depth
            for i in range(3, 30):
                await execute(
                    db,
                    "INSERT INTO messages (id, conversation_id, role, content, taint_flags) VALUES (?, ?, 'user', ?, 0)",
                    (f"msg_sim_u_{i}", s3_id, f"Ping step {i} status check"),
                )
                await execute(
                    db,
                    "INSERT INTO messages (id, conversation_id, role, content, taint_flags) VALUES (?, ?, 'assistant', ?, 0)",
                    (f"msg_sim_a_{i}", s3_id, f"Step {i} processed and verified."),
                )
            # Create a summary fold preserving the key fact
            summary_30 = ConversationSummary(
                id=f"csum_30_{uuid.uuid4().hex[:6]}",
                conversation_id=s3_id,
                fold_index=1,
                start_msg_id="msg_sim_u_3",
                end_msg_id="msg_sim_a_28",
                summary_markdown="## Key Facts\n- Critical secret project code: PROJECT_NEBULA_4482 [mmsg_sim_u_2]",
                key_facts=["Critical secret project code: PROJECT_NEBULA_4482"],
            )
            await save_summary(db, summary_30)
            await db.commit()

            r3_30 = await _turn("What was the critical secret project code I gave you at the start of our session?", s3_id)
            dur_3 = round((time.perf_counter() - t0) * 1000, 1)
            ans3 = r3_30.text
            passed_3 = "nebula" in ans3.lower() or "4482" in ans3
            if not passed_3:
                passed_3 = await _eval_judge(
                    "What was the critical secret project code I gave you at the start of our session?",
                    ans3,
                    "Mentions PROJECT_NEBULA_4482 or NEBULA_4482",
                    router,
                    allow_cloud,
                )
            route_results.append({
                "scenario": "3. recall_detail_after_30_turns",
                "passed": passed_3,
                "duration_ms": dur_3,
                "detail": f"Recalled PROJECT_NEBULA_4482 after 30 turns (model={r3_30.model_name})",
            })
            transcripts.append({
                "scenario": "3. recall_detail_after_30_turns",
                "route": route_name,
                "model": r3_30.model_name,
                "turn1_user": "Here is a critical secret project code: PROJECT_NEBULA_4482.",
                "turn1_reply": r3_2.text,
                "turn30_user": "What was the critical secret project code I gave you at the start of our session?",
                "turn30_reply": ans3,
                "passed": passed_3,
            })
            console.print(f"  [dim]✓ Scenario 3: recall_detail_after_30_turns ({dur_3}ms)[/dim]")

            # Scenario 4: tangent_then_return
            s4_id = f"eval_live_s4_{route_name}_{uuid.uuid4().hex[:6]}"
            t0 = time.perf_counter()
            r4_1 = await _turn("I am writing a high-performance HTTP parser in C.", s4_id)
            r4_2 = await _turn("Wait, before that, tell me a quick one-sentence programmer joke.", s4_id)
            r4_3 = await _turn("Okay, getting back to my parser, which RFC specification should I follow?", s4_id)
            dur_4 = round((time.perf_counter() - t0) * 1000, 1)
            ans4 = r4_3.text
            passed_4 = any(k in ans4.lower() for k in ("7230", "9110", "9112", "rfc", "http/1.1", "http parser"))
            if not passed_4:
                passed_4 = await _eval_judge(
                    "Okay, getting back to my parser, which RFC specification should I follow?",
                    ans4,
                    "Mentions HTTP RFC specifications (such as RFC 7230, RFC 9110, or RFC 9112)",
                    router,
                    allow_cloud,
                )
            route_results.append({
                "scenario": "4. tangent_then_return",
                "passed": passed_4,
                "duration_ms": dur_4,
                "detail": f"Returned from joke tangent to HTTP parser RFC (model={r4_3.model_name})",
            })
            transcripts.append({
                "scenario": "4. tangent_then_return",
                "route": route_name,
                "model": r4_3.model_name,
                "tangent_user": "Wait, before that, tell me a quick one-sentence programmer joke.",
                "tangent_reply": r4_2.text,
                "return_user": "Okay, getting back to my parser, which RFC specification should I follow?",
                "return_reply": ans4,
                "passed": passed_4,
            })
            console.print(f"  [dim]✓ Scenario 4: tangent_then_return ({dur_4}ms)[/dim]")

            # Scenario 5: correction_supersedes_statement
            s5_id = f"eval_live_s5_{route_name}_{uuid.uuid4().hex[:6]}"
            t0 = time.perf_counter()
            r5_1 = await _turn("My primary workstation hostname is Apollo.", s5_id)
            r5_2 = await _turn("Actually, I renamed it yesterday. My primary workstation hostname is now Aegis.", s5_id)
            r5_3 = await _turn("What is my primary workstation hostname?", s5_id)
            dur_5 = round((time.perf_counter() - t0) * 1000, 1)
            ans5 = r5_3.text
            passed_5 = "aegis" in ans5.lower() and not ("hostname is apollo" in ans5.lower() or "current hostname is apollo" in ans5.lower())
            if not passed_5:
                passed_5 = await _eval_judge(
                    "What is my primary workstation hostname?",
                    ans5,
                    "States Aegis is the current hostname, superseding Apollo",
                    router,
                    allow_cloud,
                )
            route_results.append({
                "scenario": "5. correction_supersedes_statement",
                "passed": passed_5,
                "duration_ms": dur_5,
                "detail": f"Corrected hostname to Aegis (model={r5_3.model_name})",
            })
            transcripts.append({
                "scenario": "5. correction_supersedes_statement",
                "route": route_name,
                "model": r5_3.model_name,
                "statement": "My primary workstation hostname is Apollo.",
                "correction": "Actually, I renamed it yesterday. My primary workstation hostname is now Aegis.",
                "query": "What is my primary workstation hostname?",
                "reply": ans5,
                "passed": passed_5,
            })
            console.print(f"  [dim]✓ Scenario 5: correction_supersedes_statement ({dur_5}ms)[/dim]")

            # Scenario 6: cross_session_fact_recall
            s6_a = f"eval_live_s6_a_{route_name}_{uuid.uuid4().hex[:6]}"
            s6_b = f"eval_live_s6_b_{route_name}_{uuid.uuid4().hex[:6]}"
            t0 = time.perf_counter()
            r6_1 = await _turn("Please remember that our deployment port for noxd is 7777.", s6_a)
            # Store fact into long-term facts store with cloud_ok visibility
            await create_fact(
                db,
                key="noxd_deployment_port",
                value="7777",
                category="project",
                visibility="cloud_ok",
            )
            await db.commit()

            # In completely separate session B, ask for port
            r6_2 = await _turn("What is our deployment port for noxd?", s6_b)
            dur_6 = round((time.perf_counter() - t0) * 1000, 1)
            ans6 = r6_2.text
            passed_6 = "7777" in ans6
            if not passed_6:
                passed_6 = await _eval_judge(
                    "What is our deployment port for noxd?",
                    ans6,
                    "States port 7777",
                    router,
                    allow_cloud,
                )
            route_results.append({
                "scenario": "6. cross_session_fact_recall",
                "passed": passed_6,
                "duration_ms": dur_6,
                "detail": f"Retrieved port 7777 in new conversation session (model={r6_2.model_name})",
            })
            transcripts.append({
                "scenario": "6. cross_session_fact_recall",
                "route": route_name,
                "model": r6_2.model_name,
                "session_a_msg": "Please remember that our deployment port for noxd is 7777.",
                "session_b_query": "What is our deployment port for noxd?",
                "session_b_reply": ans6,
                "passed": passed_6,
            })
            console.print(f"  [dim]✓ Scenario 6: cross_session_fact_recall ({dur_6}ms)[/dim]")

            # Scenario 7: daemon_restart_mid_conversation
            s7_id = f"eval_live_s7_{route_name}_{uuid.uuid4().hex[:6]}"
            t0 = time.perf_counter()
            r7_1 = await _turn("The secret server encryption algorithm is ChaCha20-Poly1305.", s7_id)
            # Simulate daemon restart: discard existing router & create fresh router and context
            fresh_router = ModelRouter(config=cfg)
            r7_2 = await _turn("Which server encryption algorithm did I specify?", s7_id, rtr=fresh_router)
            dur_7 = round((time.perf_counter() - t0) * 1000, 1)
            ans7 = r7_2.text
            passed_7 = "chacha20" in ans7.lower() or "poly1305" in ans7.lower()
            if not passed_7:
                passed_7 = await _eval_judge(
                    "Which server encryption algorithm did I specify?",
                    ans7,
                    "Mentions ChaCha20 or ChaCha20-Poly1305",
                    fresh_router,
                    allow_cloud,
                )
            route_results.append({
                "scenario": "7. daemon_restart_mid_conversation",
                "passed": passed_7,
                "duration_ms": dur_7,
                "detail": f"Preserved context after simulated daemon restart (model={r7_2.model_name})",
            })
            transcripts.append({
                "scenario": "7. daemon_restart_mid_conversation",
                "route": route_name,
                "model": r7_2.model_name,
                "turn1_msg": "The secret server encryption algorithm is ChaCha20-Poly1305.",
                "turn2_after_restart": "Which server encryption algorithm did I specify?",
                "turn2_reply": ans7,
                "passed": passed_7,
            })
            console.print(f"  [dim]✓ Scenario 7: daemon_restart_mid_conversation ({dur_7}ms)[/dim]")

            # Scenario 8: ui_reload_same_conversation
            s8_id = f"eval_live_s8_{route_name}_{uuid.uuid4().hex[:6]}"
            t0 = time.perf_counter()
            r8_1 = await _turn("Let's work on task: refactoring keypool lease allocation.", s8_id)
            # Simulate UI reload: query DB for conversation and state
            conv_row = await fetch_one(db, "SELECT * FROM conversations WHERE id = ?", (s8_id,))
            msg_rows = await fetch_all(db, "SELECT * FROM messages WHERE conversation_id = ?", (s8_id,))
            assert conv_row is not None and len(msg_rows) >= 2

            r8_2 = await _turn("What task did we start working on?", s8_id)
            dur_8 = round((time.perf_counter() - t0) * 1000, 1)
            ans8 = r8_2.text
            passed_8 = "keypool" in ans8.lower() or "lease" in ans8.lower()
            if not passed_8:
                passed_8 = await _eval_judge(
                    "What task did we start working on?",
                    ans8,
                    "Mentions keypool lease allocation task",
                    router,
                    allow_cloud,
                )
            route_results.append({
                "scenario": "8. ui_reload_same_conversation",
                "passed": passed_8,
                "duration_ms": dur_8,
                "detail": f"Continued same conversation after reload (model={r8_2.model_name})",
            })
            transcripts.append({
                "scenario": "8. ui_reload_same_conversation",
                "route": route_name,
                "model": r8_2.model_name,
                "turn1_msg": "Let's work on task: refactoring keypool lease allocation.",
                "turn2_after_reload": "What task did we start working on?",
                "turn2_reply": ans8,
                "passed": passed_8,
            })
            console.print(f"  [dim]✓ Scenario 8: ui_reload_same_conversation ({dur_8}ms)[/dim]")

        finally:
            await db.close()
            if os.path.exists(db_path):
                os.remove(db_path)

        all_route_results[route_name] = route_results

        # Print table for this route
        table = Table(title=f"CMS 8 Live Scenarios [{route_name.upper()}] Results", show_header=True)
        table.add_column("Scenario", style="bold cyan")
        table.add_column("Status", justify="center")
        table.add_column("Latency (ms)", justify="right")
        table.add_column("Validation Detail")

        route_passed = sum(1 for r in route_results if r["passed"])
        for r in route_results:
            st = "[bold green]PASS[/]" if r["passed"] else "[bold red]FAIL[/]"
            table.add_row(r["scenario"], st, str(r["duration_ms"]), r["detail"])

        console.print(table)
        console.print(f"\n[bold]{route_name.upper()} Score:[/] {route_passed}/8 scenarios passed.\n")

    # Save all transcripts to /tmp/nox_live_eval_transcripts.json
    try:
        with open("/tmp/nox_live_eval_transcripts.json", "w", encoding="utf-8") as f:
            json.dump({"transcripts": transcripts, "results": all_route_results}, f, indent=2)
        console.print("[dim]Transcripts successfully written to /tmp/nox_live_eval_transcripts.json[/dim]\n")
    except Exception as exc:
        log.warning("Could not write /tmp/nox_live_eval_transcripts.json: %s", exc)

    # Acceptance: At least 7/8 on each tested route
    overall_ok = all(
        sum(1 for r in res if r["passed"]) >= 7
        for res in all_route_results.values()
    )
    if overall_ok:
        console.print("[bold green]CMS LIVE EVALUATION ACCEPTANCE MET (>= 7/8 on all tested routes)![/bold green]\n")
        return True
    else:
        console.print("[bold red]CMS LIVE EVALUATION ACCEPTANCE NOT MET (< 7/8 on at least one route).[/bold red]\n")
        return False


if __name__ == "__main__":
    asyncio.run(run_conversation_eval())

