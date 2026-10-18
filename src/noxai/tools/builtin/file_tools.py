"""File manipulation built-in tools with path traversal and symlink guards."""

from __future__ import annotations

import difflib
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from noxai.tools.registry import ToolContext, ToolResult, tool
from noxai.tools.safety import SecurityViolation, validate_path

# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class ReadFileParams(BaseModel):
    model_config = {"extra": "forbid"}

    path: str = Field(..., description="Absolute or relative file path to read.", min_length=1)
    max_bytes: int = Field(
        default=200 * 1024,
        description="Maximum bytes to read (capped at 200 KB).",
        ge=1,
        le=200 * 1024,
    )


@tool(
    name="read_file",
    description="Read the text content of a file within allowed file roots.",
    params=ReadFileParams,
    tier="read",
    untrusted_output=True,  # File content is untrusted data and taints context
    timeout=10.0,
)
async def read_file(params: ReadFileParams, ctx: ToolContext) -> ToolResult:
    try:
        resolved_path = validate_path(
            params.path,
            allowed_roots=ctx.config.safety.allowed_file_roots,
            denied_paths=ctx.config.safety.denied_paths,
            check_exists=True,
        )
    except (SecurityViolation, FileNotFoundError) as exc:
        return ToolResult(ok=False, output=str(exc), summary="Path validation failed")

    if not resolved_path.is_file():
        return ToolResult(
            ok=False,
            output=f"Path is not a regular file: {resolved_path}",
            summary="Not a file",
        )

    # Check for binary file by reading initial chunk and inspecting for null bytes
    try:
        with open(resolved_path, "rb") as f:
            header = f.read(1024)
            if b"\x00" in header:
                return ToolResult(
                    ok=False,
                    output=f"Refusing to read binary file: {resolved_path}",
                    summary="Binary file refused",
                )
            f.seek(0)
            data = f.read(params.max_bytes)
            text = data.decode("utf-8", errors="replace")

        truncated = len(data) == params.max_bytes
        summary_msg = f"Read {len(text)} characters from {resolved_path.name}"
        if truncated:
            text += "\n[File truncated at 200 KB limit]"
            summary_msg += " (truncated)"

        return ToolResult(
            ok=True,
            output=text,
            summary=summary_msg,
            sources=[{"title": resolved_path.name, "url": f"file://{resolved_path}"}],
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            output=f"Failed to read file {resolved_path}: {exc}",
            summary="File read error",
        )


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


class ListFilesParams(BaseModel):
    model_config = {"extra": "forbid"}

    directory: str = Field(
        default=".",
        description="Directory path to inspect within allowed file roots.",
    )
    max_entries: int = Field(
        default=50,
        description="Maximum entries to return.",
        ge=1,
        le=200,
    )


@tool(
    name="list_files",
    description="List files and directories in a folder within allowed roots.",
    params=ListFilesParams,
    tier="read",
    timeout=10.0,
)
async def list_files(params: ListFilesParams, ctx: ToolContext) -> ToolResult:
    try:
        resolved_dir = validate_path(
            params.directory,
            allowed_roots=ctx.config.safety.allowed_file_roots,
            denied_paths=ctx.config.safety.denied_paths,
            check_exists=True,
        )
    except (SecurityViolation, FileNotFoundError) as exc:
        return ToolResult(ok=False, output=str(exc), summary="Path validation failed")

    if not resolved_dir.is_dir():
        return ToolResult(
            ok=False,
            output=f"Path is not a directory: {resolved_dir}",
            summary="Not a directory",
        )

    try:
        entries = sorted(resolved_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        count = 0
        for entry in entries:
            if count >= params.max_entries:
                lines.append(f"... and {len(entries) - count} more items")
                break
            # Skip hidden items unless explicitly asked
            if entry.name.startswith(".") and not params.directory.startswith("."):
                continue
            indicator = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{indicator}")
            count += 1

        output_str = "\n".join(lines) if lines else "(Empty directory)"
        return ToolResult(
            ok=True,
            output=output_str,
            summary=f"Found {len(entries)} items in {resolved_dir.name}",
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            output=f"Error listing directory {resolved_dir}: {exc}",
            summary="List files error",
        )


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class WriteFileParams(BaseModel):
    model_config = {"extra": "forbid"}

    path: str = Field(..., description="Destination file path to write to.", min_length=1)
    content: str = Field(..., description="The content to write into the file.")


@tool(
    name="write_file",
    description="Write content to a file. Requires owner approval and displays diff for changes.",
    params=WriteFileParams,
    tier="write",
    timeout=15.0,
)
async def write_file(params: WriteFileParams, ctx: ToolContext) -> ToolResult:
    try:
        resolved_path = validate_path(
            params.path,
            allowed_roots=ctx.config.safety.allowed_file_roots,
            denied_paths=ctx.config.safety.denied_paths,
            check_exists=False,
        )
    except SecurityViolation as exc:
        return ToolResult(ok=False, output=str(exc), summary="Path security violation")

    # Ensure parent directory exists
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    # Compute diff if file already exists
    diff_text = ""
    is_new = not resolved_path.exists()
    if not is_new:
        try:
            old_content = resolved_path.read_text(encoding="utf-8", errors="replace")
            diff_lines = list(
                difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    params.content.splitlines(keepends=True),
                    fromfile=f"a/{resolved_path.name}",
                    tofile=f"b/{resolved_path.name}",
                )
            )
            diff_text = "".join(diff_lines)
        except Exception:
            diff_text = "[Could not compute diff against existing file]"

    # Atomic write via temporary file
    try:
        temp_dir = resolved_path.parent
        with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
            tf.write(params.content)
            temp_path = Path(tf.name)

        # Atomic replacement
        temp_path.replace(resolved_path)

        summary_msg = (
            f"Created new file: {resolved_path.name}"
            if is_new
            else f"Updated file: {resolved_path.name}"
        )
        output_msg = f"Successfully wrote {len(params.content)} characters to {resolved_path}."
        if diff_text:
            output_msg += f"\n\nDiff:\n{diff_text}"

        return ToolResult(
            ok=True,
            output=output_msg,
            summary=summary_msg,
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            output=f"Error writing file {resolved_path}: {exc}",
            summary="File write failed",
        )
