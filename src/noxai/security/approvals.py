"""Approvals Center for NoX.

Handles pending write actions (send mail, post message, create issue, re-run
workflow, install skill, run script) with:
- Precise preview and scope.
- Expiration time.
- Single-use scoped approval from UI, CLI, or confirmed chat channel.
- Payload hashing to guarantee that approved payload matches execution payload.
- Full tamper-evident audit logging.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one, get_db
from noxai.security.audit import log_audit_event

log = logging.getLogger(__name__)


@dataclass
class PendingApproval:
    """Represents a pending write action requiring user authorization."""

    id: str
    action_type: str
    payload: dict[str, Any]
    preview: str
    scope: str
    confirmation_code: str
    status: str  # 'pending', 'approved', 'denied', 'consumed', 'expired', 'revoked'
    expires_at: str
    created_at: str
    resolved_at: str | None = None
    resolved_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action_type": self.action_type,
            "payload": self.payload,
            "preview": self.preview,
            "scope": self.scope,
            "confirmation_code": self.confirmation_code,
            "status": self.status,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
        }


class ApprovalsCenter:
    """Manages creation, review, verification, and consumption of scoped approvals."""

    @classmethod
    async def request_approval(
        cls,
        action_type: str,
        payload: dict[str, Any],
        preview: str,
        scope: str,
        ttl_seconds: int = 3600,
        confirmation_code: str | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> PendingApproval:
        """Create a new pending approval and log it."""
        approval_id = f"appr_{uuid.uuid4().hex[:12]}"
        code = confirmation_code or f"{100000 + secrets.randbelow(900000)}"
        now = datetime.now(UTC)
        created_at = now.strftime("%Y-%m-%dT%H:%M:%fZ")
        expires_at = (now + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%dT%H:%M:%fZ")
        payload_json = json.dumps(payload, sort_keys=True)

        async def _insert(conn: aiosqlite.Connection) -> None:
            await execute(
                conn,
                """
                INSERT INTO pending_approvals (
                    id, action_type, payload_json, preview, scope, confirmation_code,
                    status, expires_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    approval_id,
                    action_type,
                    payload_json,
                    preview,
                    scope,
                    code,
                    expires_at,
                    created_at,
                ),
            )
            await conn.commit()

        if db is not None:
            await _insert(db)
        else:
            async with get_db() as conn:
                await _insert(conn)

        await log_audit_event(
            actor="system",
            action=f"approval_requested:{action_type}",
            target=scope,
            decision="pending",
            summary_redacted=f"Requested approval {approval_id}: {preview[:120]}",
            db=db,
        )

        return PendingApproval(
            id=approval_id,
            action_type=action_type,
            payload=payload,
            preview=preview,
            scope=scope,
            confirmation_code=code,
            status="pending",
            expires_at=expires_at,
            created_at=created_at,
        )

    @classmethod
    async def get_approval(
        cls,
        approval_id: str,
        db: aiosqlite.Connection | None = None,
    ) -> PendingApproval | None:
        """Fetch an approval by ID."""
        async def _get(conn: aiosqlite.Connection) -> PendingApproval | None:
            row = await fetch_one(
                conn,
                """
                SELECT id, action_type, payload_json, preview, scope, confirmation_code,
                       status, expires_at, created_at, resolved_at, resolved_by
                FROM pending_approvals WHERE id = ?
                """,
                (approval_id,),
            )
            if row is None:
                return None
            try:
                payload = json.loads(row["payload_json"])
            except Exception:
                payload = {}
            return PendingApproval(
                id=row["id"],
                action_type=row["action_type"],
                payload=payload,
                preview=row["preview"],
                scope=row["scope"],
                confirmation_code=row["confirmation_code"] or "",
                status=row["status"],
                expires_at=row["expires_at"],
                created_at=row["created_at"],
                resolved_at=row["resolved_at"],
                resolved_by=row["resolved_by"],
            )

        if db is not None:
            return await _get(db)
        else:
            async with get_db() as conn:
                return await _get(conn)

    @classmethod
    async def approve(
        cls,
        approval_id: str,
        actor: str = "user",
        confirmation_code: str | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> PendingApproval:
        """Approve a pending action. Raises ValueError if code invalid or expired."""
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%fZ")

        async def _approve(conn: aiosqlite.Connection) -> PendingApproval:
            appr = await cls.get_approval(approval_id, db=conn)
            if appr is None:
                raise ValueError(f"Approval not found: {approval_id}")

            if appr.status != "pending":
                raise ValueError(f"Approval {approval_id} is already in status '{appr.status}'")

            if appr.expires_at < now_str:
                await execute(
                    conn,
                    "UPDATE pending_approvals SET status = 'expired', resolved_at = ? WHERE id = ?",
                    (now_str, approval_id),
                )
                await conn.commit()
                raise ValueError(f"Approval {approval_id} has expired")

            if confirmation_code and confirmation_code != appr.confirmation_code:
                raise ValueError("Confirmation code does not match")

            await execute(
                conn,
                """
                UPDATE pending_approvals
                SET status = 'approved', resolved_at = ?, resolved_by = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now_str, actor, approval_id),
            )
            await conn.commit()

            appr.status = "approved"
            appr.resolved_at = now_str
            appr.resolved_by = actor
            return appr

        if db is not None:
            result = await _approve(db)
        else:
            async with get_db() as conn:
                result = await _approve(conn)

        await log_audit_event(
            actor=actor,
            action="approval_granted",
            target=approval_id,
            decision="approved",
            summary_redacted=f"Granted approval {approval_id} for {result.scope}",
            db=db,
        )
        return result

    @classmethod
    async def deny(
        cls,
        approval_id: str,
        actor: str = "user",
        reason: str = "",
        db: aiosqlite.Connection | None = None,
    ) -> PendingApproval:
        """Deny a pending action."""
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%fZ")

        async def _deny(conn: aiosqlite.Connection) -> PendingApproval:
            appr = await cls.get_approval(approval_id, db=conn)
            if appr is None:
                raise ValueError(f"Approval not found: {approval_id}")

            if appr.status != "pending":
                raise ValueError(f"Approval {approval_id} is already in status '{appr.status}'")

            await execute(
                conn,
                """
                UPDATE pending_approvals
                SET status = 'denied', resolved_at = ?, resolved_by = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now_str, actor, approval_id),
            )
            await conn.commit()

            appr.status = "denied"
            appr.resolved_at = now_str
            appr.resolved_by = actor
            return appr

        if db is not None:
            result = await _deny(db)
        else:
            async with get_db() as conn:
                result = await _deny(conn)

        await log_audit_event(
            actor=actor,
            action="approval_denied",
            target=approval_id,
            decision="denied",
            summary_redacted=f"Denied approval {approval_id}: {reason or 'No reason specified'}",
            db=db,
        )
        return result

    @classmethod
    async def verify_and_consume(
        cls,
        approval_id: str,
        action_type: str,
        payload: dict[str, Any],
        db: aiosqlite.Connection | None = None,
    ) -> bool:
        """Verify that an approved action matches exact payload, then consume it (single-use)."""
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%fZ")

        async def _consume(conn: aiosqlite.Connection) -> bool:
            appr = await cls.get_approval(approval_id, db=conn)
            if appr is None:
                log.warning("Consumption failed: approval %s not found", approval_id)
                return False

            if appr.status != "approved":
                log.warning(
                    "Consumption failed: approval %s in status '%s'",
                    approval_id,
                    appr.status,
                )
                return False

            if appr.action_type != action_type:
                log.warning("Consumption failed: approval %s action_type mismatch", approval_id)
                return False

            # Strict canonical payload check
            payload_canonical = json.dumps(payload, sort_keys=True)
            stored_canonical = json.dumps(appr.payload, sort_keys=True)
            if payload_canonical != stored_canonical:
                log.warning("Consumption failed: payload mismatch for approval %s", approval_id)
                await log_audit_event(
                    actor="system",
                    action="approval_payload_mismatch",
                    target=approval_id,
                    decision="blocked",
                    summary_redacted=f"Payload mismatch for approval {approval_id}",
                    db=conn,
                )
                return False

            # Mark consumed (single use)
            await execute(
                conn,
                """
                UPDATE pending_approvals
                SET status = 'consumed', resolved_at = ?
                WHERE id = ? AND status = 'approved'
                """,
                (now_str, approval_id),
            )
            await conn.commit()
            return True

        if db is not None:
            ok = await _consume(db)
        else:
            async with get_db() as conn:
                ok = await _consume(conn)

        if ok:
            await log_audit_event(
                actor="system",
                action="approval_consumed",
                target=approval_id,
                decision="executed",
                summary_redacted=f"Single-use approval {approval_id} consumed",
                db=db,
            )
        return ok

    @classmethod
    async def revoke_all_pending(
        cls,
        actor: str = "kill_switch",
        db: aiosqlite.Connection | None = None,
    ) -> int:
        """Revoke all pending approvals across the system (used by kill switch)."""
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%fZ")

        async def _revoke(conn: aiosqlite.Connection) -> int:
            cursor = await execute(
                conn,
                """
                UPDATE pending_approvals
                SET status = 'revoked', resolved_at = ?, resolved_by = ?
                WHERE status = 'pending'
                """,
                (now_str, actor),
            )
            count = cursor.rowcount if hasattr(cursor, "rowcount") and cursor.rowcount > 0 else 0

            # Also revoke any legacy pending tool approvals
            try:
                await execute(
                    conn,
                    """
                    UPDATE approvals
                    SET status = 'denied', decided_at = ?, decided_by = ?
                    WHERE status = 'pending'
                    """,
                    (now_str, actor),
                )
            except Exception as exc:
                log.debug("Legacy tool approvals revocation note: %s", exc)

            await conn.commit()
            return count

        if db is not None:
            count = await _revoke(db)
        else:
            async with get_db() as conn:
                count = await _revoke(conn)

        await log_audit_event(
            actor=actor,
            action="approvals_revoked_all",
            target="pending_approvals",
            decision="revoked",
            summary_redacted=f"Revoked {count} pending approvals",
            db=db,
        )
        return count

    @classmethod
    async def list_approvals(
        cls,
        status: str | None = None,
        limit: int = 50,
        db: aiosqlite.Connection | None = None,
    ) -> list[PendingApproval]:
        """List approvals, optionally filtered by status."""
        async def _list(conn: aiosqlite.Connection) -> list[PendingApproval]:
            if status:
                rows = await fetch_all(
                    conn,
                    """
                    SELECT id, action_type, payload_json, preview, scope, confirmation_code,
                           status, expires_at, created_at, resolved_at, resolved_by
                    FROM pending_approvals WHERE status = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (status, limit),
                )
            else:
                rows = await fetch_all(
                    conn,
                    """
                    SELECT id, action_type, payload_json, preview, scope, confirmation_code,
                           status, expires_at, created_at, resolved_at, resolved_by
                    FROM pending_approvals
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (limit,),
                )

            results: list[PendingApproval] = []
            for r in rows:
                try:
                    p = json.loads(r["payload_json"])
                except Exception:
                    p = {}
                results.append(
                    PendingApproval(
                        id=r["id"],
                        action_type=r["action_type"],
                        payload=p,
                        preview=r["preview"],
                        scope=r["scope"],
                        confirmation_code=r["confirmation_code"] or "",
                        status=r["status"],
                        expires_at=r["expires_at"],
                        created_at=r["created_at"],
                        resolved_at=r["resolved_at"],
                        resolved_by=r["resolved_by"],
                    )
                )
            return results

        if db is not None:
            return await _list(db)
        else:
            async with get_db() as conn:
                return await _list(conn)
