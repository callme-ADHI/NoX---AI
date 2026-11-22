"""Permission manifests and runtime enforcement for NoX skills, agents, and connectors.

Enforces a default-deny model: every entity must declare its allowed read, write,
execute, and network access. Violations are logged to the tamper-evident audit log
and rejected.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any

from noxai.security.audit import log_audit_event_sync

log = logging.getLogger(__name__)


class PermissionDeniedError(PermissionError):
    """Raised when an action violates an entity's permission manifest."""

    def __init__(self, entity_id: str, category: str, item: str, reason: str = "") -> None:
        self.entity_id = entity_id
        self.category = category
        self.item = item
        self.reason = reason or f"Permission denied: {entity_id} cannot access {category}:{item}"
        super().__init__(self.reason)


@dataclass
class PermissionManifest:
    """Explicit security declaration for a skill, agent, or connector."""

    id: str
    name: str
    manifest_type: str = "skill"  # "skill" | "agent" | "connector" | "system"
    read_resources: list[str] = field(default_factory=list)
    write_resources: list[str] = field(default_factory=list)
    execute_commands: list[str] = field(default_factory=list)
    network_hosts: list[str] = field(default_factory=list)
    allow_cloud_egress: bool = False
    allow_shell: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "manifest_type": self.manifest_type,
            "read_resources": self.read_resources,
            "write_resources": self.write_resources,
            "execute_commands": self.execute_commands,
            "network_hosts": self.network_hosts,
            "allow_cloud_egress": self.allow_cloud_egress,
            "allow_shell": self.allow_shell,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PermissionManifest:
        return cls(
            id=data.get("id", "unknown"),
            name=data.get("name", "Unknown"),
            manifest_type=data.get("manifest_type", "skill"),
            read_resources=list(data.get("read_resources", [])),
            write_resources=list(data.get("write_resources", [])),
            execute_commands=list(data.get("execute_commands", [])),
            network_hosts=list(data.get("network_hosts", [])),
            allow_cloud_egress=bool(data.get("allow_cloud_egress", False)),
            allow_shell=bool(data.get("allow_shell", False)),
        )


def _pattern_matches(pattern: str, item: str) -> bool:
    if pattern == "*" or pattern == item:
        return True
    return fnmatch.fnmatch(item.lower(), pattern.lower())


class PermissionEnforcer:
    """Evaluates and enforces permissions against an entity's manifest."""

    @classmethod
    def check_permission(
        cls,
        manifest: PermissionManifest | None,
        category: str,
        item: str,
    ) -> bool:
        """Check if item is permitted in category ('read', 'write', 'execute', 'network', 'cloud').

        Returns True if allowed, False if denied (and logs the denial).
        """
        entity_id = manifest.id if manifest else "untrusted"

        if manifest is None:
            reason = "No permission manifest provided (default deny)"
            cls._log_denial(entity_id, category, item, reason)
            return False

        allowed = False

        if category == "read":
            allowed = any(_pattern_matches(pat, item) for pat in manifest.read_resources)
        elif category == "write":
            allowed = any(_pattern_matches(pat, item) for pat in manifest.write_resources)
        elif category == "execute":
            if manifest.allow_shell:
                allowed = True
            else:
                allowed = any(_pattern_matches(pat, item) for pat in manifest.execute_commands)
        elif category == "network":
            allowed = any(_pattern_matches(pat, item) for pat in manifest.network_hosts)
        elif category == "cloud":
            allowed = manifest.allow_cloud_egress
        else:
            allowed = False

        if not allowed:
            reason = f"Action {category}:{item} not in declared manifest"
            cls._log_denial(entity_id, category, item, reason)
            return False

        return True

    @classmethod
    def enforce(
        cls,
        manifest: PermissionManifest | None,
        category: str,
        item: str,
    ) -> None:
        """Enforce permission or raise PermissionDeniedError."""
        if not cls.check_permission(manifest, category, item):
            entity_id = manifest.id if manifest else "untrusted"
            raise PermissionDeniedError(entity_id, category, item)

    @classmethod
    def _log_denial(cls, entity_id: str, category: str, item: str, reason: str) -> None:
        try:
            log_audit_event_sync(
                actor=f"perm:{entity_id}",
                action=f"permission_denied:{category}",
                target=item,
                decision="denied",
                summary_redacted=reason,
            )
        except Exception as exc:
            log.warning("Failed to record permission denial in audit log: %s", exc)
