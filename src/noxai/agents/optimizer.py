"""Learning loop prompt optimizer with offline evaluation, auto-revert safety, and telemetry."""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from noxai.db import get_db
from noxai.security.audit import log_audit_event

log = logging.getLogger(__name__)


class PromptOptimizer:
    """Manages prompt optimization cycles, offline benchmark scoring, and auto-revert safety."""

    def __init__(
        self,
        evaluator: Callable[[str, list[dict[str, str]]], float] | None = None,
    ) -> None:
        self._evaluator = evaluator

    @classmethod
    def default_score_eval(cls, prompt: str, cases: list[dict[str, str]]) -> float:
        """Heuristic offline evaluation scoring completeness, clarity, and safety."""
        score = 0.0
        for _c in cases:
            # Reward clarity, domain markers, and safety constraints
            lower = prompt.lower()
            if "you are" in lower:
                score += 20.0
            if "format" in lower or "markdown" in lower:
                score += 20.0
            if "concise" in lower or "accurate" in lower:
                score += 20.0
            if "privacy" in lower or "sensitive" in lower:
                score += 20.0
            # Length sanity check (not too short, not overflowing)
            if 50 <= len(prompt.split()) <= 200:
                score += 20.0
        return score / max(1, len(cases))

    async def evaluate_candidate(
        self,
        target_name: str,
        candidate_prompt: str,
        test_cases: list[dict[str, str]] | None = None,
        target_type: str = "agent",
    ) -> dict[str, Any]:
        """Evaluate a candidate prompt against baseline with 3-regression auto-revert."""
        cases = test_cases or [
            {"input": "Summarize today's intelligence", "expected": "structured markdown"},
            {"input": "Help write a python script", "expected": "clean python code"},
        ]

        # 1. Fetch current baseline prompt & regression count
        baseline_prompt = ""
        prev_regressions = 0

        async with get_db() as db:
            async with db.execute(
                "SELECT prompt_template FROM agents WHERE name = ?", (target_name,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    baseline_prompt = row[0]

            async with db.execute(
                """
                SELECT regression_count, status FROM prompt_optimizations
                WHERE target_name = ? ORDER BY created_at DESC LIMIT 1
                """,
                (target_name,),
            ) as cur:
                opt_row = await cur.fetchone()
                if opt_row and opt_row[1] == "evaluating":
                    prev_regressions = opt_row[0]

        if not baseline_prompt:
            baseline_prompt = candidate_prompt

        # 2. Score baseline and candidate
        if self._evaluator:
            baseline_score = self._evaluator(baseline_prompt, cases)
            candidate_score = self._evaluator(candidate_prompt, cases)
        else:
            baseline_score = self.default_score_eval(baseline_prompt, cases)
            candidate_score = self.default_score_eval(candidate_prompt, cases)

        score_delta = round(candidate_score - baseline_score, 2)
        opt_id = f"opt_{secrets.token_hex(6)}"
        now = datetime.now(UTC).isoformat()

        # 3. Handle regression or promotion
        status = "evaluating"
        current_regressions = prev_regressions

        if score_delta < 0:
            current_regressions += 1
            if current_regressions >= 3:
                # AUTO-REVERT TRIGGERED
                status = "reverted"
                log.warning(
                    "prompt_optimization_auto_revert target=%s regressions=%d",
                    target_name,
                    current_regressions,
                )
                async with get_db() as db:
                    await db.execute(
                        "UPDATE agents SET prompt_template = ? WHERE name = ?",
                        (baseline_prompt, target_name),
                    )
                    await db.commit()

                await log_audit_event(
                    actor="optimizer",
                    action="prompt_auto_revert",
                    target=target_name,
                    decision="reverted",
                    summary_redacted=(
                        f"Auto-reverted candidate prompt for {target_name} after 3 regressions "
                        f"(delta={score_delta})"
                    ),
                )
        else:
            # Candidate performed equal or better
            current_regressions = 0
            if score_delta > 0:
                status = "promoted"
                async with get_db() as db:
                    await db.execute(
                        "UPDATE agents SET prompt_template = ? WHERE name = ?",
                        (candidate_prompt, target_name),
                    )
                    await db.commit()

        # Record optimization log in DB
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO prompt_optimizations (
                    id, target_type, target_name, baseline_prompt, candidate_prompt,
                    score_delta, regression_count, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opt_id,
                    target_type,
                    target_name,
                    baseline_prompt,
                    candidate_prompt,
                    score_delta,
                    current_regressions,
                    status,
                    now,
                ),
            )
            await db.commit()

        return {
            "opt_id": opt_id,
            "target": target_name,
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "score_delta": score_delta,
            "regression_count": current_regressions,
            "status": status,
        }


class TelemetryTracker:
    """Aggregates and formats runtime telemetry metrics from execution traces."""

    @classmethod
    async def get_metrics(cls, hours: int = 24) -> dict[str, Any]:
        """Aggregate trace token counts, latency, and route distribution."""
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, started_at, finished_at, engine, model, token_counts,
                       final_status, error_text
                FROM traces
                WHERE started_at >= datetime('now', '-' || ? || ' hours')
                ORDER BY started_at DESC
                """,
                (hours,),
            ) as cur:
                rows = list(await cur.fetchall())

        total_traces = len(rows)
        if total_traces == 0:
            return {
                "period_hours": hours,
                "total_traces": 0,
                "success_count": 0,
                "failure_count": 0,
                "total_tokens": 0,
                "avg_latency_ms": 0,
                "route_distribution": {},
                "estimated_cost_usd": 0.0,
            }

        succeeded = 0
        failed = 0
        total_tokens = 0
        latencies_ms: list[float] = []
        routes: dict[str, int] = {}

        for r in rows:
            status = r[6]
            if status == "success":
                succeeded += 1
            else:
                failed += 1

            # Engine route
            eng = r[3] or "local"
            routes[eng] = routes.get(eng, 0) + 1

            # Tokens
            if r[5]:
                try:
                    tc = json.loads(r[5]) if isinstance(r[5], str) else r[5]
                    total_tokens += sum(tc.values()) if isinstance(tc, dict) else int(tc)
                except Exception as exc:
                    log.debug("telemetry_token_parse_error: %s", exc)

            # Latency
            if r[1] and r[2]:
                try:
                    t1 = datetime.fromisoformat(r[1])
                    t2 = datetime.fromisoformat(r[2])
                    latencies_ms.append((t2 - t1).total_seconds() * 1000)
                except Exception as exc:
                    log.debug("telemetry_latency_parse_error: %s", exc)

        avg_latency = round(sum(latencies_ms) / max(1, len(latencies_ms)), 1)
        latencies_ms.sort()
        p90_latency = latencies_ms[int(len(latencies_ms) * 0.9)] if latencies_ms else 0.0

        # Estimated cost ($0.002 per 1k tokens for cloud fallback)
        cost = round((total_tokens / 1000.0) * 0.002, 4)

        return {
            "period_hours": hours,
            "total_traces": total_traces,
            "success_count": succeeded,
            "failure_count": failed,
            "total_tokens": total_tokens,
            "avg_latency_ms": avg_latency,
            "p90_latency_ms": round(p90_latency, 1),
            "route_distribution": routes,
            "estimated_cost_usd": cost,
        }
