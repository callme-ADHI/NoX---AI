"""Kill switch ('nx panic') implementation for NoX.

Immediately terminates/suspends:
- Connectors background sync.
- Social channels listener/polling.
- Continuous background agents.
- Revokes all pending approvals.
- Records a tamper-evident audit event.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from noxai.db import execute, fetch_one, get_db
from noxai.security.approvals import ApprovalsCenter
from noxai.security.audit import log_audit_event

log = logging.getLogger(__name__)


class KillSwitch:
    """Manages the emergency kill switch and panic state."""

    @classmethod
    async def is_active(cls, db: aiosqlite.Connection | None = None) -> bool:
        """Check if the system is currently in panic mode."""
        async def _check(conn: aiosqlite.Connection) -> bool:
            row = await fetch_one(
                conn,
                "SELECT value FROM security_state WHERE key = 'panic_engaged'",
            )
            return bool(row and row["value"] == "1")

        if db is not None:
            return await _check(db)
        else:
            async with get_db() as conn:
                return await _check(conn)

    @classmethod
    async def get_state(cls, db: aiosqlite.Connection | None = None) -> dict[str, Any]:
        """Get full details of current kill switch state."""
        async def _get(conn: aiosqlite.Connection) -> dict[str, Any]:
            rows = await fetch_one(
                conn,
                "SELECT value FROM security_state WHERE key = 'panic_engaged'",
            )
            engaged = bool(rows and rows["value"] == "1")

            reason_row = await fetch_one(
                conn,
                "SELECT value FROM security_state WHERE key = 'panic_reason'",
            )
            time_row = await fetch_one(
                conn,
                "SELECT value FROM security_state WHERE key = 'panic_engaged_at'",
            )
            actor_row = await fetch_one(
                conn,
                "SELECT value FROM security_state WHERE key = 'panic_actor'",
            )

            return {
                "engaged": engaged,
                "reason": reason_row["value"] if reason_row else "",
                "engaged_at": time_row["value"] if time_row else "",
                "actor": actor_row["value"] if actor_row else "",
            }

        if db is not None:
            return await _get(db)
        else:
            async with get_db() as conn:
                return await _get(conn)

    @classmethod
    async def engage(
        cls,
        reason: str = "manual_panic",
        actor: str = "user",
        db: aiosqlite.Connection | None = None,
    ) -> None:
        """Engage the kill switch. Revokes approvals and sets panic flag."""
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%fZ")

        async def _engage(conn: aiosqlite.Connection) -> None:
            for k, v in [
                ("panic_engaged", "1"),
                ("panic_reason", reason),
                ("panic_engaged_at", now_str),
                ("panic_actor", actor),
            ]:
                await execute(
                    conn,
                    """
                    INSERT INTO security_state (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (k, v, now_str),
                )
            await conn.commit()

        if db is not None:
            await _engage(db)
        else:
            async with get_db() as conn:
                await _engage(conn)

        # Revoke all pending approvals
        revoked_count = await ApprovalsCenter.revoke_all_pending(
            actor=f"kill_switch:{actor}", db=db
        )

        # Record audit event
        summary = (
            f"Emergency kill switch engaged: {reason} "
            f"(revoked {revoked_count} approvals)"
        )
        await log_audit_event(
            actor=actor,
            action="panic_engaged",
            target="system",
            decision="executed",
            summary_redacted=summary,
            db=db,
        )
        log.warning("EMERGENCY KILL SWITCH ENGAGED by %s: %s", actor, reason)

    @classmethod
    async def resume(
        cls,
        actor: str = "user",
        db: aiosqlite.Connection | None = None,
    ) -> None:
        """Resume normal operations from panic state."""
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%fZ")

        async def _resume(conn: aiosqlite.Connection) -> None:
            for k, v in [
                ("panic_engaged", "0"),
                ("panic_reason", ""),
            ]:
                await execute(
                    conn,
                    """
                    INSERT INTO security_state (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (k, v, now_str),
                )
            await conn.commit()

        if db is not None:
            await _resume(db)
        else:
            async with get_db() as conn:
                await _resume(conn)

        # Record audit event
        await log_audit_event(
            actor=actor,
            action="panic_resumed",
            target="system",
            decision="executed",
            summary_redacted="Emergency kill switch resumed normal operations",
            db=db,
        )
        log.info("Kill switch resumed by %s", actor)
