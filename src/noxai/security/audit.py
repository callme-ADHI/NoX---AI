"""Tamper-evident, hash-chained audit logging for NoX security foundation.

Every connector read/write, approval, cloud egress, permission denial, kill switch
event, and deletion is recorded in the append-only ``audit_events`` table.
Each row is cryptographically chained to its predecessor with SHA-256:
    hash = SHA-256(prev_hash | id | ts | actor | action | target | decision | summary_redacted)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one, get_db, lastrowid
from noxai.security.guardrails import OutputScanner, PIIRedactor

log = logging.getLogger(__name__)

GENESIS_PREV_HASH = "0" * 64


def compute_event_hash(
    prev_hash: str,
    event_id: int,
    ts: str,
    actor: str,
    action: str,
    target: str,
    decision: str,
    summary_redacted: str,
) -> str:
    """Compute the SHA-256 hash of an audit log entry."""
    payload = f"{prev_hash}|{event_id}|{ts}|{actor}|{action}|{target}|{decision}|{summary_redacted}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def log_audit_event(
    actor: str,
    action: str,
    target: str,
    decision: str,
    summary_redacted: str,
    db: aiosqlite.Connection | None = None,
) -> int:
    """Append a tamper-evident, hash-chained event to audit_events.

    Ensures no raw secrets or PII leak into the summary.
    """
    # 1. Cleanse summary of any PII or secrets
    cleaned, _ = PIIRedactor.redact(summary_redacted)
    out_scan = OutputScanner.scan(cleaned)
    final_summary = out_scan.sanitized_text if out_scan.flagged else cleaned

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%fZ")

    async def _insert(conn: aiosqlite.Connection) -> int:
        # Fetch the latest audit event to find previous hash and next id
        row = await fetch_one(
            conn,
            "SELECT id, hash FROM audit_events ORDER BY id DESC LIMIT 1",
        )
        if row is None:
            prev_hash = GENESIS_PREV_HASH
            next_id = 1
        else:
            prev_hash = row["hash"]
            next_id = int(row["id"]) + 1

        event_hash = compute_event_hash(
            prev_hash=prev_hash,
            event_id=next_id,
            ts=ts,
            actor=actor,
            action=action,
            target=target,
            decision=decision,
            summary_redacted=final_summary,
        )

        cursor = await execute(
            conn,
            """
            INSERT INTO audit_events (
                id, ts, actor, action, target, decision, summary_redacted, prev_hash, hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                next_id,
                ts,
                actor,
                action,
                target,
                decision,
                final_summary,
                prev_hash,
                event_hash,
            ),
        )
        await conn.commit()
        inserted_id = await lastrowid(cursor)
        return int(inserted_id) if inserted_id is not None else next_id

    if db is not None:
        return await _insert(db)
    else:
        async with get_db() as conn:
            return await _insert(conn)


def log_audit_event_sync(
    actor: str,
    action: str,
    target: str,
    decision: str,
    summary_redacted: str,
) -> int:
    """Synchronous helper for log_audit_event."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Schedule in current loop or run in thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run,
                log_audit_event(actor, action, target, decision, summary_redacted),
            ).result()
    else:
        return asyncio.run(
            log_audit_event(actor, action, target, decision, summary_redacted)
        )


async def verify_audit_chain(
    db: aiosqlite.Connection | None = None,
) -> tuple[bool, int, str]:
    """Verify cryptographic integrity of the entire audit_events table.

    Returns:
        (is_valid, count_verified, message)
    """
    async def _verify(conn: aiosqlite.Connection) -> tuple[bool, int, str]:
        rows = await fetch_all(
            conn,
            """
            SELECT id, ts, actor, action, target, decision, summary_redacted, prev_hash, hash
            FROM audit_events ORDER BY id ASC
            """,
        )
        if not rows:
            return True, 0, "Audit log is empty (valid)"

        expected_prev = GENESIS_PREV_HASH
        for row in rows:
            r_id = int(row["id"])
            r_prev = row["prev_hash"]
            r_hash = row["hash"]

            if r_prev != expected_prev:
                msg = (
                    f"Tampering detected: prev_hash mismatch at audit event ID {r_id} "
                    f"(expected {expected_prev[:12]}..., got {r_prev[:12]}...)"
                )
                return False, r_id, msg

            computed = compute_event_hash(
                prev_hash=r_prev,
                event_id=r_id,
                ts=row["ts"],
                actor=row["actor"],
                action=row["action"],
                target=row["target"],
                decision=row["decision"],
                summary_redacted=row["summary_redacted"],
            )

            if computed != r_hash:
                msg = (
                    f"Tampering detected: hash mismatch at audit event ID {r_id} "
                    f"(computed {computed[:12]}..., stored {r_hash[:12]}...)"
                )
                return False, r_id, msg

            expected_prev = r_hash

        return (
            True,
            len(rows),
            f"Audit log verified: {len(rows)} events chained without tampering",
        )

    if db is not None:
        return await _verify(db)
    else:
        async with get_db() as conn:
            return await _verify(conn)


def verify_audit_chain_sync() -> tuple[bool, int, str]:
    """Synchronous helper for verify_audit_chain."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, verify_audit_chain()).result()
    else:
        return asyncio.run(verify_audit_chain())


async def get_recent_audit_events(
    limit: int = 50,
    db: aiosqlite.Connection | None = None,
) -> list[dict[str, Any]]:
    """Retrieve recent audit events ordered by ID desc."""
    async def _fetch(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
        rows = await fetch_all(
            conn,
            """
            SELECT id, ts, actor, action, target, decision, summary_redacted, prev_hash, hash
            FROM audit_events ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]

    if db is not None:
        return await _fetch(db)
    else:
        async with get_db() as conn:
            return await _fetch(conn)
