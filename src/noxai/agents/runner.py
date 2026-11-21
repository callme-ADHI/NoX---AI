"""Multi-mode agent runner for NoX supporting on-demand, scheduled, and continuous execution."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from noxai.agents.spec import AgentSpec, get_builtin_agents
from noxai.db import get_db
from noxai.security.audit import log_audit_event
from noxai.security.kill_switch import KillSwitch

log = logging.getLogger(__name__)


class AgentRunner:
    """Supervises and executes autonomous agents across execution modes."""

    def __init__(
        self,
        executor: Callable[[AgentSpec, str], tuple[str, int, float]] | None = None,
    ) -> None:
        self._executor = executor
        self._continuous_tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_events: dict[str, asyncio.Event] = {}

    async def get_agent(self, agent_name: str) -> AgentSpec | None:
        """Fetch agent spec from DB or built-in registry."""
        builtins = get_builtin_agents()
        if agent_name in builtins:
            return builtins[agent_name]

        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, name, mode, cron_expression, enabled, config_json,
                       prompt_template, budget_tokens, budget_cost_usd
                FROM agents WHERE name = ?
                """,
                (agent_name,),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    import json
                    return AgentSpec(
                        id=row[0],
                        name=row[1],
                        description=row[1],
                        mode=row[2],
                        cron_expression=row[3],
                        enabled=bool(row[4]),
                        config=json.loads(row[5] or "{}"),
                        prompt_template=row[6],
                        budget_tokens=row[7],
                        budget_cost_usd=row[8],
                    )
        return None

    async def run_agent(
        self,
        agent_name_or_spec: str | AgentSpec,
        input_prompt: str,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        """Execute an agent run with budget and killswitch enforcement."""
        # 1. Kill Switch Check
        if await KillSwitch.is_active():
            raise PermissionError("Kill switch is active. Agent execution blocked.")

        # 2. Resolve AgentSpec
        if isinstance(agent_name_or_spec, str):
            spec = await self.get_agent(agent_name_or_spec)
            if not spec:
                raise ValueError(f"Agent '{agent_name_or_spec}' not found.")
        else:
            spec = agent_name_or_spec

        if not spec.enabled:
            raise PermissionError(f"Agent '{spec.name}' is currently disabled.")

        run_id = f"arun_{secrets.token_hex(6)}"
        start_time = time.monotonic()
        now = datetime.now(UTC).isoformat()

        # Record run start in DB
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO agent_runs (
                    id, agent_id, trigger, status, input_prompt, created_at
                ) VALUES (?, ?, ?, 'running', ?, ?)
                """,
                (run_id, spec.id, trigger, input_prompt, now),
            )
            await db.execute(
                "UPDATE agents SET status = 'running', last_run_at = ? WHERE id = ?",
                (now, spec.id),
            )
            await db.commit()

        # 3. Execution (via injected executor or default synthetic reasoning)
        status = "succeeded"
        output_result = ""
        error_msg = None
        tokens_used = 0
        cost_usd = 0.0

        try:
            if self._executor:
                output_result, tokens_used, cost_usd = self._executor(spec, input_prompt)
            else:
                # Default baseline execution
                tokens_used = min(spec.budget_tokens, len(input_prompt.split()) * 10 + 100)
                cost_usd = round(tokens_used * 0.000002, 6)
                output_result = (
                    f"[{spec.name.upper()} REPORT]\n"
                    f"Processed input: '{input_prompt[:80]}...'\n"
                    f"Executed under role: {spec.description}"
                )

            # Check budget constraints
            if tokens_used > spec.budget_tokens:
                raise ValueError(
                    f"Execution exceeded token budget: {tokens_used} > {spec.budget_tokens}"
                )
            if cost_usd > spec.budget_cost_usd:
                raise ValueError(
                    f"Execution exceeded cost budget: ${cost_usd:.4f} > ${spec.budget_cost_usd:.4f}"
                )

        except Exception as exc:
            status = "failed"
            error_msg = str(exc)
            log.error("agent_execution_failed agent=%s error=%s", spec.name, exc)

        duration_ms = int((time.monotonic() - start_time) * 1000)
        finish_time = datetime.now(UTC).isoformat()

        # Update run in DB
        async with get_db() as db:
            await db.execute(
                """
                UPDATE agent_runs SET
                    status = ?,
                    output_result = ?,
                    tokens_used = ?,
                    cost_usd = ?,
                    duration_ms = ?,
                    error_msg = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    output_result,
                    tokens_used,
                    cost_usd,
                    duration_ms,
                    error_msg,
                    finish_time,
                    run_id,
                ),
            )
            await db.execute(
                "UPDATE agents SET status = 'idle', last_error = ? WHERE id = ?",
                (error_msg, spec.id),
            )
            await db.commit()

        await log_audit_event(
            actor="agent_runner",
            action=f"agent_run_{spec.name}",
            target=spec.id,
            decision=status,
            summary_redacted=f"Agent {spec.name} executed via {trigger}: {status}",
        )

        return {
            "run_id": run_id,
            "agent": spec.name,
            "status": status,
            "output": output_result,
            "tokens_used": tokens_used,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
            "error": error_msg,
        }

    async def start_continuous_agent(
        self,
        agent_name: str,
        interval_sec: int = 10,
        task_prompt: str = "Continuous watchdog loop",
    ) -> None:
        """Launch continuous agent supervisor task."""
        if agent_name in self._continuous_tasks:
            return

        stop_ev = asyncio.Event()
        self._stop_events[agent_name] = stop_ev

        async def _loop() -> None:
            log.info("continuous_agent_started agent=%s", agent_name)
            while not stop_ev.is_set():
                try:
                    if not await KillSwitch.is_active():
                        await self.run_agent(agent_name, task_prompt, trigger="continuous")
                except Exception as exc:
                    log.warning("continuous_agent_cycle_error agent=%s error=%s", agent_name, exc)
                try:
                    await asyncio.wait_for(stop_ev.wait(), timeout=interval_sec)
                except TimeoutError:
                    pass
            log.info("continuous_agent_stopped agent=%s", agent_name)

        task = asyncio.create_task(_loop())
        self._continuous_tasks[agent_name] = task

    async def stop_continuous_agent(self, agent_name: str) -> None:
        """Stop a running continuous agent task."""
        stop_ev = self._stop_events.get(agent_name)
        if stop_ev:
            stop_ev.set()
        task = self._continuous_tasks.get(agent_name)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._continuous_tasks.pop(agent_name, None)
            self._stop_events.pop(agent_name, None)
