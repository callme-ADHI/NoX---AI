"""Safety rules, command filters, path guards, SSRF protection, and approvals."""

from __future__ import annotations

import ipaddress
import re
import shlex
import socket
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

Tier = Literal["read", "write", "dangerous", "forbidden"]


class SecurityViolation(Exception):
    """Raised when an operation violates security rules or contains forbidden patterns."""


class SSRFViolation(SecurityViolation):
    """Raised when a URL attempts to access forbidden/private IP spaces."""


# ---------------------------------------------------------------------------
# Shell Command Safety
# ---------------------------------------------------------------------------

_FORBIDDEN_RAW_PATTERNS = [
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),  # fork bomb
    re.compile(
        r"rm\s+(-[a-z]*r[a-z]*f[a-z]*|-[a-z]*f[a-z]*r[a-z]*)\s+/(?:\s|$|/|\*)", re.IGNORECASE
    ),  # rm -rf /
    re.compile(
        r"rm\s+(-[a-z]*r[a-z]*f[a-z]*|-[a-z]*f[a-z]*r[a-z]*)\s+~(?:\s|$|/|\*)", re.IGNORECASE
    ),  # rm -rf ~
    re.compile(
        r"rm\s+(-[a-z]*r[a-z]*f[a-z]*|-[a-z]*f[a-z]*r[a-z]*)\s+\*(?:\s|$)", re.IGNORECASE
    ),  # rm -rf *
    re.compile(r"mkfs(?:\.[a-z0-9]+)?\s+", re.IGNORECASE),  # mkfs
    re.compile(
        r"dd\s+.*of=/dev/(?:sd[a-z]|nvme[0-9]n[0-9]|hd[a-z]|vd[a-z]|null|zero)", re.IGNORECASE
    ),  # dd to dev
    re.compile(
        r">\s*/dev/(?:sd[a-z]|nvme[0-9]n[0-9]|hd[a-z]|vd[a-z])", re.IGNORECASE
    ),  # overwrite disk
    re.compile(r"chmod\s+(-[a-z]*R[a-z]*\s+)?777\s+/(?:\s|$)", re.IGNORECASE),  # chmod 777 /
    re.compile(r"chown\s+-[a-z]*R[a-z]*\s+.*(?:\s|/)/(?:\s|$)", re.IGNORECASE),  # chown -R on /
    re.compile(r"(?:curl|wget)\s+.*\|\s*(?:sh|bash|zsh|dash)", re.IGNORECASE),  # curl | sh
    re.compile(r"\|\s*(?:sh|bash|zsh|dash)\s*$", re.IGNORECASE),  # | bash pipe
    re.compile(r"pacman\s+-[a-z]*R[a-z]*", re.IGNORECASE),  # pacman -R / -Rns
    # sudo / root escalations
    re.compile(r"(?:^|\s)(?:sudo|su|visudo|passwd)(?:\s|$)", re.IGNORECASE),
    re.compile(r"/etc/(?:passwd|shadow|sudoers)", re.IGNORECASE),  # sensitive system files
    re.compile(
        r"systemctl\s+(?:disable|mask)\s+.*(?:noxd|nox|ollama|ufw|iptables|firewalld)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:~|\$HOME)/\.ssh(?:\s|/|$)", re.IGNORECASE),  # ssh keys
    re.compile(r"secrets\.env", re.IGNORECASE),  # secrets file
]

_DANGEROUS_PATTERNS = [
    re.compile(r"^(?:shutdown|reboot|poweroff)(?:\s|$)", re.IGNORECASE),
    re.compile(r"^systemctl\s+(?:poweroff|reboot|halt)(?:\s|$)", re.IGNORECASE),
]

_SHELL_OPERATORS = [";", "&&", "||", "|", "`", "$(", ">", "<", "&"]

# Binaries permitted to run automatically in 'read' tier when called without shell operators
_READ_ALLOWLIST_BINARIES = {
    "ls",
    "cat",
    "df",
    "du",
    "free",
    "uptime",
    "uname",
    "whoami",
    "date",
    "ps",
    "lsblk",
    "ip",
    "ss",
    "id",
    "hostname",
    "pacman",  # only with -Q*
    "systemctl",  # only status / is-active / is-enabled
    "journalctl",
    "hyprctl",
    "nvidia-smi",
    "sensors",
}


def evaluate_shell_command(cmd_str: str) -> tuple[Tier, str | None]:
    """Inspect shell command and determine its security tier.

    Returns:
        (tier, reason) where tier is 'read', 'write', 'dangerous', or 'forbidden'.
    """
    clean_cmd = cmd_str.strip()
    if not clean_cmd:
        return "read", None

    # 1. Check forbidden raw patterns
    for pat in _FORBIDDEN_RAW_PATTERNS:
        if pat.search(clean_cmd):
            return "forbidden", f"Command matches forbidden pattern: {pat.pattern}"

    # 2. Check dangerous tier patterns (reboot, shutdown)
    for pat in _DANGEROUS_PATTERNS:
        if pat.search(clean_cmd):
            return "dangerous", "System power state modification requires explicit confirmation."

    # 3. Check for shell operators (pipes, redirects, sequences)
    has_operator = any(op in clean_cmd for op in _SHELL_OPERATORS)

    # 4. Parse arguments
    try:
        argv = shlex.split(clean_cmd)
    except ValueError as exc:
        return "forbidden", f"Malformed command string: {exc}"

    if not argv:
        return "read", None

    binary = argv[0].lower()
    # Strip path if full path is provided (e.g. /usr/bin/ls -> ls)
    base_binary = Path(binary).name

    # Check parsed arguments for forbidden tokens
    for arg in argv:
        if arg.lower() in ("sudo", "su", "visudo", "passwd"):
            return "forbidden", f"Command contains forbidden token: {arg}"
        sensitive_targets = (".ssh", "secrets.env", "/etc/shadow", "/etc/sudoers", "/etc/passwd")
        if any(target in arg for target in sensitive_targets):
            return "forbidden", f"Access to sensitive path forbidden in command: {arg}"

    # If any operator is present, it pushes out of read tier into write tier
    if has_operator:
        return "write", "Command uses shell operators (pipes, redirects, or chaining)."

    # 5. Check if binary is in read allowlist
    if base_binary in _READ_ALLOWLIST_BINARIES:
        # Check subcommands for pacman (-Q only)
        if base_binary == "pacman":
            if len(argv) > 1 and argv[1].startswith("-Q"):
                return "read", None
            return "write", "pacman operations other than -Q require approval."

        # Check subcommands for systemctl (status, is-active, is-enabled only)
        if base_binary == "systemctl":
            if len(argv) > 1 and argv[1] in ("status", "is-active", "is-enabled"):
                return "read", None
            return "write", "systemctl commands modifying services require approval."

        # Check subcommands for journalctl
        if base_binary == "journalctl":
            return "read", None

        return "read", None

    # Everything else defaults to write tier (requires approval)
    return "write", f"Command binary '{base_binary}' requires approval."


# ---------------------------------------------------------------------------
# Path Guard
# ---------------------------------------------------------------------------


def validate_path(
    path_str: str,
    allowed_roots: list[str],
    denied_paths: list[str],
    check_exists: bool = False,
) -> Path:
    """Validate that path is within allowed roots and outside denied paths.

    Resolves symlinks to their canonical destinations to prevent symlink bypass.
    """
    try:
        path = Path(path_str).expanduser()
        # Resolve path (even if it does not exist yet)
        resolved = path.resolve()
    except Exception as exc:
        raise SecurityViolation(f"Invalid path '{path_str}': {exc}") from exc

    if check_exists and not resolved.exists():
        raise FileNotFoundError(f"Path does not exist: {resolved}")

    # Check denied paths first
    for denied in denied_paths:
        denied_resolved = Path(denied).expanduser().resolve()
        if resolved == denied_resolved or denied_resolved in resolved.parents:
            raise SecurityViolation(
                f"Access denied: Path '{resolved}' is inside restricted directory "
                f"'{denied_resolved}'."
            )

    # Check allowed roots
    allowed = False
    for root in allowed_roots:
        root_resolved = Path(root).expanduser().resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            allowed = True
            break

    if not allowed:
        raise SecurityViolation(
            f"Access denied: Path '{resolved}' is outside allowed file roots {allowed_roots}."
        )

    return resolved


# ---------------------------------------------------------------------------
# SSRF Guard
# ---------------------------------------------------------------------------

_CARRIER_GRADE_NAT = ipaddress.ip_network("100.64.0.0/10")
_METADATA_IP = ipaddress.ip_address("169.254.169.254")


def check_ssrf_url(url_str: str) -> str:
    """Validate that a URL is HTTP/HTTPS and resolves only to public, safe IP addresses.

    Raises SSRFViolation if URL points to loopback, private RFC1918, link-local,
    metadata IP, or carrier-grade NAT.
    """
    try:
        parsed = urlparse(url_str)
    except Exception as exc:
        raise SSRFViolation(f"Malformed URL: {exc}") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise SSRFViolation(f"Scheme '{scheme}' is forbidden. Only HTTP and HTTPS are permitted.")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFViolation("URL is missing a valid hostname.")

    # Check raw IP literals or resolve hostname via DNS
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFViolation(f"Failed to resolve hostname '{hostname}': {exc}") from exc

    for info in addr_infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]

        ip = ipaddress.ip_address(ip_str)

        if ip == _METADATA_IP:
            raise SSRFViolation(
                f"SSRF blocked: Hostname '{hostname}' ({ip}) points to cloud metadata service."
            )

        if ip.is_loopback:
            raise SSRFViolation(f"SSRF blocked: Hostname '{hostname}' ({ip}) points to loopback.")

        # Check NAT64 prefix (64:ff9b::/96)
        if ip.version == 6 and ip in ipaddress.ip_network("64:ff9b::/96"):
            embedded_v4 = ipaddress.IPv4Address(ip.packed[-4:])
            if embedded_v4 == _METADATA_IP:
                raise SSRFViolation(
                    f"SSRF blocked: Hostname '{hostname}' points to metadata via NAT64."
                )
            if embedded_v4.is_loopback or embedded_v4.is_private or embedded_v4.is_link_local:
                raise SSRFViolation(
                    f"SSRF blocked: Hostname '{hostname}' points to private IPv4 via NAT64."
                )
        elif ip.is_reserved:
            raise SSRFViolation(
                f"SSRF blocked: Hostname '{hostname}' ({ip}) points to reserved address space."
            )

        if ip.is_private or ip.is_link_local:
            raise SSRFViolation(
                f"SSRF blocked: Hostname '{hostname}' ({ip}) "
                "points to private RFC1918 address space."
            )

        if ip.is_multicast:
            raise SSRFViolation(
                f"SSRF blocked: Hostname '{hostname}' ({ip}) points to multicast address space."
            )

        if ip.is_unspecified:
            raise SSRFViolation(
                f"SSRF blocked: Hostname '{hostname}' ({ip}) points to unspecified address."
            )

        if ip.version == 4 and ip in _CARRIER_GRADE_NAT:
            raise SSRFViolation(
                f"SSRF blocked: Hostname '{hostname}' ({ip}) points to carrier-grade NAT space."
            )

    return url_str


# ---------------------------------------------------------------------------
# Secret Redaction
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"nvapi-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"ghp_[A-Za-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(r"password['\"]?\s*[:=]\s*['\"]?[^'\"\s]+", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Mask known secret and token patterns in string."""
    redacted = text
    for pat in _SECRET_PATTERNS:
        redacted = pat.sub("[REDACTED_SECRET]", redacted)
    return redacted
