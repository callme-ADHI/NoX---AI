"""Egress filter and audit ledger for NoX Privacy Policy v2.

Every prompt, message list, or context block destined for an external / cloud
model provider must pass through this egress filter choke point (§1.2, §1.3).

If any SECRET or LOCAL_ONLY content is present after filtering and masking,
or if private mode is enabled, or if chat_to_cloud is disabled, cloud egress
is blocked and rerouted to the local model.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from noxai.config import Settings
from noxai.db import execute, get_db, lastrowid
from noxai.models.base import Message, PrivacyViolation
from noxai.security.sensitivity import (
    is_sensitive_prompt,
    mask_secrets,
    scan_for_secrets,
)
from noxai.taint import Content, DataClass, TaintFlag

log = logging.getLogger(__name__)


class EgressBlockedError(PrivacyViolation):
    """Raised when egress to a cloud model is blocked by the egress filter."""


@dataclass
class EgressDecision:
    """The result of evaluating a payload against the egress filter."""

    allowed: bool
    decision: str  # "allowed" | "blocked" | "rerouted_local"
    reason: str
    data_class: DataClass
    violations: list[str] = field(default_factory=list)
    sanitized_messages: list[Message] | None = None
    prompt_tokens_est: int = 0
    sha256_hash: str = ""


class EgressFilter:
    """Choke point for enforcing Privacy Policy v2 on all cloud-bound traffic."""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config

    def evaluate(
        self,
        messages: list[Message],
        *,
        provider_name: str,
        model: str,
        is_local: bool,
        chat_to_cloud: bool | None = None,
        private_mode: bool | None = None,
        allow_masking: bool = True,
    ) -> EgressDecision:
        """Evaluate a message sequence before dispatching to a model provider.

        Args:
            messages: The list of ``Message`` objects to inspect.
            provider_name: Name of the provider (e.g. "ollama", "nvidia").
            model: The target model name.
            is_local: True if the provider runs on-device (e.g. Ollama).
            chat_to_cloud: Override for ``privacy.chat_to_cloud``.
            private_mode: Override for ``privacy.private_mode``.
            allow_masking: If True, masks discovered secrets in CHAT messages.
        """
        # 1. Local execution is always permitted (G2 / §1.2)
        if is_local:
            total_text = "".join(m.get_model_text() for m in messages)
            return EgressDecision(
                allowed=True,
                decision="allowed",
                reason="Local provider execution",
                data_class=DataClass.LOCAL_ONLY,
                sanitized_messages=messages,
                prompt_tokens_est=max(1, len(total_text) // 4),
                sha256_hash=hashlib.sha256(total_text.encode("utf-8")).hexdigest(),
            )

        # 2. Resolve settings
        if private_mode is None:
            private_mode = getattr(getattr(self.config, "privacy", None), "private_mode", False)
        if chat_to_cloud is None:
            chat_to_cloud = getattr(getattr(self.config, "privacy", None), "chat_to_cloud", True)

        total_text = "".join(m.get_model_text() for m in messages)
        sha256_hash = hashlib.sha256(total_text.encode("utf-8")).hexdigest()
        prompt_tokens_est = max(1, len(total_text) // 4)

        # 3. Private Mode check: zero cloud egress
        if private_mode:
            return EgressDecision(
                allowed=False,
                decision="rerouted_local",
                reason="Private mode is active (zero cloud egress)",
                data_class=DataClass.LOCAL_ONLY,
                violations=["private_mode is active"],
                prompt_tokens_est=prompt_tokens_est,
                sha256_hash=sha256_hash,
            )

        # 4. Inspect each message, tool call, and argument
        violations: list[str] = []
        max_data_class = DataClass.PUBLIC
        sanitized_messages: list[Message] = []
        has_chat = False
        is_sensitive = False
        sensitive_reason = ""

        for msg in messages:
            # Classification and provenance
            if isinstance(msg.content, str):
                # Plain unlabeled string -> fail closed as LOCAL_ONLY (§1.2)
                violations.append(
                    f"Plain unlabeled string in role '{msg.role}' "
                    "treated as LOCAL_ONLY (fail closed)"
                )
                if max_data_class.rank < DataClass.LOCAL_ONLY.rank:
                    max_data_class = DataClass.LOCAL_ONLY
                text_to_scan = msg.content
            elif isinstance(msg.content, Content):
                dc = msg.content.get_data_class()
                if dc.rank > max_data_class.rank:
                    max_data_class = dc

                if dc == DataClass.SECRET:
                    violations.append(
                        f"Message role '{msg.role}' is explicitly classified as SECRET"
                    )
                elif dc == DataClass.LOCAL_ONLY or bool(msg.content.taint & TaintFlag.PRIVATE):
                    violations.append(
                        f"Message role '{msg.role}' contains LOCAL_ONLY / PRIVATE data"
                    )
                elif dc == DataClass.CHAT:
                    has_chat = True

                text_to_scan = msg.content.text
            else:
                text_to_scan = str(msg.content)

            # Check tool calls
            for tc in msg.tool_calls:
                if getattr(tc, "taint", TaintFlag.NONE) & TaintFlag.PRIVATE:
                    violations.append(f"Tool call '{tc.name}' carries PRIVATE taint")
                    if max_data_class.rank < DataClass.LOCAL_ONLY.rank:
                        max_data_class = DataClass.LOCAL_ONLY
                if isinstance(tc.arguments, dict):
                    for k, v in tc.arguments.items():
                        if isinstance(v, Content) and (
                            v.is_private()
                            or v.get_data_class() in (DataClass.SECRET, DataClass.LOCAL_ONLY)
                        ):
                            violations.append(
                                f"Tool call '{tc.name}' argument '{k}' carries PRIVATE taint"
                            )
                            if max_data_class.rank < DataClass.LOCAL_ONLY.rank:
                                max_data_class = DataClass.LOCAL_ONLY

            # Secret scanning and masking
            secrets = scan_for_secrets(text_to_scan)
            final_text = text_to_scan
            if secrets:
                if allow_masking and max_data_class != DataClass.SECRET:
                    masked = mask_secrets(text_to_scan)
                    final_text = masked
                    if isinstance(msg.content, Content):
                        new_content = Content(
                            text=masked,
                            taint=msg.content.taint,
                            source=msg.content.source,
                            data_class=msg.content.data_class or DataClass.CHAT,
                        )
                    else:
                        new_content = Content.chat(masked)
                    sanitized_messages.append(
                        Message(
                            role=msg.role,
                            content=new_content,
                            tool_calls=msg.tool_calls,
                            tool_call_id=msg.tool_call_id,
                            name=msg.name,
                        )
                    )
                else:
                    violations.append(
                        f"Unmasked secret '{secrets[0].pattern_name}' in message role '{msg.role}'"
                    )
                    sanitized_messages.append(msg)
            else:
                sanitized_messages.append(msg)

            # Heuristic sensitivity check on user prompts (on post-masking text)
            if msg.role == "user":
                sens, s_reason = is_sensitive_prompt(final_text)
                if sens:
                    is_sensitive = True
                    sensitive_reason = s_reason or "Sensitive user prompt"

        # 5. Check chat_to_cloud setting
        if has_chat and not chat_to_cloud:
            return EgressDecision(
                allowed=False,
                decision="rerouted_local",
                reason="Cloud chat disabled by owner (privacy.chat_to_cloud is false)",
                data_class=max_data_class,
                violations=["privacy.chat_to_cloud is false"],
                prompt_tokens_est=prompt_tokens_est,
                sha256_hash=sha256_hash,
            )

        # 6. Sensitive prompt heuristic -> reroute to local
        if is_sensitive:
            return EgressDecision(
                allowed=False,
                decision="rerouted_local",
                reason=f"Sensitive prompt detected: {sensitive_reason}",
                data_class=DataClass.LOCAL_ONLY,
                violations=[f"sensitive: {sensitive_reason}"],
                prompt_tokens_est=prompt_tokens_est,
                sha256_hash=sha256_hash,
            )

        # 7. Check for hard violations (SECRET, LOCAL_ONLY)
        if violations:
            log.warning(
                "EgressFilter blocked cloud request: max_data_class=%s, violations=%s",
                max_data_class,
                violations,
            )
            return EgressDecision(
                allowed=False,
                decision="blocked",
                reason="; ".join(violations),
                data_class=max_data_class,
                violations=violations,
                prompt_tokens_est=prompt_tokens_est,
                sha256_hash=sha256_hash,
            )

        # 8. All clear
        return EgressDecision(
            allowed=True,
            decision="allowed",
            reason="Passed egress filter",
            data_class=max_data_class,
            sanitized_messages=sanitized_messages,
            prompt_tokens_est=prompt_tokens_est,
            sha256_hash=sha256_hash,
        )


# ---------------------------------------------------------------------------
# Egress Ledger Persistence
# ---------------------------------------------------------------------------


async def record_egress_decision(
    decision: EgressDecision,
    provider: str,
    model: str,
    db: aiosqlite.Connection | None = None,
) -> int | None:
    """Record an audit entry in the egress_ledger table."""

    async def _write(conn: aiosqlite.Connection) -> int | None:
        try:
            cursor = await execute(
                conn,
                """
                INSERT INTO egress_ledger (
                    provider, model, prompt_tokens_est, data_class, decision, reason, sha256_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    model,
                    decision.prompt_tokens_est,
                    decision.data_class.value,
                    decision.decision,
                    decision.reason,
                    decision.sha256_hash,
                ),
            )
            await conn.commit()
            return await lastrowid(cursor)
        except Exception as exc:
            log.debug("egress_ledger record note: %s", exc)
            return None

    if db is not None:
        return await _write(db)
    else:
        try:
            async with get_db() as conn:
                return await _write(conn)
        except Exception as exc:
            log.debug("egress_ledger connection note: %s", exc)
            return None


async def get_recent_egress_records(
    limit: int = 50,
    db: aiosqlite.Connection | None = None,
) -> list[dict[str, Any]]:
    """Retrieve recent entries from the egress ledger."""
    from noxai.db import fetch_all

    sql = """
        SELECT id, timestamp, provider, model, prompt_tokens_est,
               data_class, decision, reason, sha256_hash, created_at
        FROM egress_ledger
        ORDER BY id DESC
        LIMIT ?
    """

    async def _fetch(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
        rows = await fetch_all(conn, sql, (limit,))
        return [dict(r) for r in rows]

    if db is not None:
        return await _fetch(db)
    else:
        async with get_db() as conn:
            return await _fetch(conn)
