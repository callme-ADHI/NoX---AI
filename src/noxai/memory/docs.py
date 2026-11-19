# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Markdown heading chunker, read-only incremental crawler, and FTS5 document recall (Workstream D2).

"""Document Memory and Notes Recall (Workstream D2).

Provides:
- Incremental indexing of configured read-only roots (~/Documents/Obsidian Vault, ~/Documents/Study)
- Markdown heading chunking (~800 chars with ~100 char overlap, retaining heading hierarchy)
- Strict read-only guarantee over source roots
- SQLite FTS5 BM25 keyword search with snippet generation
- Query API, stats, and roots management
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
import uuid

import aiosqlite

from noxai.config import get_config
from noxai.db import execute, fetch_all, fetch_one, get_db

log = logging.getLogger(__name__)

TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".org",
    ".py",
    ".js",
    ".ts",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".rs",
    ".go",
    ".sh",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sql",
    ".html",
    ".css",
}

MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100


@dataclass
class DocumentChunk:
    chunk_index: int
    text: str
    heading_path: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int


@dataclass
class ChunkMatch:
    document_id: str
    path: str
    source_root: str
    heading_path: str
    text: str
    snippet: str
    score: float
    line_start: int
    line_end: int
    char_start: int
    char_end: int


@dataclass
class IndexingStats:
    scanned: int = 0
    indexed: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    chunks_created: int = 0
    duration_seconds: float = 0.0
    roots: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["roots"] = self.roots or []
        return d


def chunk_markdown(content: str, target_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[DocumentChunk]:
    """Split markdown text into chunks respecting headings and paragraph boundaries."""
    if not content:
        return []

    lines = content.splitlines(keepends=True)
    chunks: list[DocumentChunk] = []

    # Heading hierarchy tracker: list of (level, title)
    heading_stack: list[tuple[int, str]] = []

    # Current section accumulator
    section_lines: list[tuple[int, int, str]] = []  # (line_num, char_offset, text)
    current_char_offset = 0

    def get_current_heading_path() -> str:
        return " > ".join(title for _, title in heading_stack)

    def flush_section(lines_data: list[tuple[int, int, str]], heading_path: str) -> None:
        if not lines_data:
            return
        section_text = "".join(l[2] for l in lines_data)
        if not section_text.strip():
            return

        base_char_start = lines_data[0][1]
        base_line_start = lines_data[0][0]

        # If section is small enough, make single chunk
        if len(section_text) <= target_size:
            chunks.append(
                DocumentChunk(
                    chunk_index=len(chunks),
                    text=section_text.strip(),
                    heading_path=heading_path,
                    char_start=base_char_start,
                    char_end=base_char_start + len(section_text),
                    line_start=base_line_start,
                    line_end=lines_data[-1][0],
                )
            )
            return

        # Split section into paragraphs
        paragraphs = re.split(r"(\n\s*\n)", section_text)
        current_chunk_paras: list[str] = []
        current_len = 0
        chunk_para_start_offset = 0

        p_offset = 0
        for p in paragraphs:
            p_len = len(p)
            if current_len + p_len > target_size and current_chunk_paras:
                c_text = "".join(current_chunk_paras).strip()
                if c_text:
                    c_start = base_char_start + chunk_para_start_offset
                    c_end = c_start + len(c_text)
                    # Estimate line numbers
                    sub_before = section_text[:chunk_para_start_offset]
                    l_start = base_line_start + sub_before.count("\n")
                    l_end = l_start + c_text.count("\n")

                    chunks.append(
                        DocumentChunk(
                            chunk_index=len(chunks),
                            text=c_text,
                            heading_path=heading_path,
                            char_start=c_start,
                            char_end=c_end,
                            line_start=l_start,
                            line_end=l_end,
                        )
                    )

                # Overlap: keep trailing text up to overlap characters
                if overlap > 0 and current_chunk_paras:
                    overlap_acc = []
                    acc_len = 0
                    for op in reversed(current_chunk_paras):
                        if acc_len + len(op) <= overlap:
                            overlap_acc.insert(0, op)
                            acc_len += len(op)
                        else:
                            break
                    current_chunk_paras = overlap_acc
                    current_len = sum(len(x) for x in current_chunk_paras)
                    chunk_para_start_offset = max(0, p_offset - current_len)
                else:
                    current_chunk_paras = []
                    current_len = 0
                    chunk_para_start_offset = p_offset

            current_chunk_paras.append(p)
            current_len += p_len
            p_offset += p_len

        if current_chunk_paras:
            c_text = "".join(current_chunk_paras).strip()
            if c_text:
                c_start = base_char_start + chunk_para_start_offset
                sub_before = section_text[:chunk_para_start_offset]
                l_start = base_line_start + sub_before.count("\n")
                l_end = l_start + c_text.count("\n")
                chunks.append(
                    DocumentChunk(
                        chunk_index=len(chunks),
                        text=c_text,
                        heading_path=heading_path,
                        char_start=c_start,
                        char_end=c_start + len(c_text),
                        line_start=l_start,
                        line_end=l_end,
                    )
                )

    heading_regex = re.compile(r"^(#{1,6})\s+(.+)$")

    for idx, line in enumerate(lines, 1):
        line_offset = current_char_offset
        current_char_offset += len(line)

        m = heading_regex.match(line.strip())
        if m:
            # Heading found: flush current section under previous heading
            flush_section(section_lines, get_current_heading_path())
            section_lines = []

            level = len(m.group(1))
            title = m.group(2).strip()

            # Update heading stack
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))

        section_lines.append((idx, line_offset, line))

    # Flush final section
    flush_section(section_lines, get_current_heading_path())
    return chunks


def chunk_generic_text(content: str, target_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[DocumentChunk]:
    """Chunk generic source code or plain text files."""
    if not content:
        return []

    lines = content.splitlines(keepends=True)
    chunks: list[DocumentChunk] = []
    current_lines: list[str] = []
    current_len = 0
    start_line = 1
    char_start = 0
    current_char_offset = 0

    for idx, line in enumerate(lines, 1):
        line_len = len(line)
        if current_len + line_len > target_size and current_lines:
            c_text = "".join(current_lines).strip()
            if c_text:
                chunks.append(
                    DocumentChunk(
                        chunk_index=len(chunks),
                        text=c_text,
                        heading_path="",
                        char_start=char_start,
                        char_end=char_start + len(c_text),
                        line_start=start_line,
                        line_end=idx - 1,
                    )
                )
            # Handle overlap
            if overlap > 0 and len(current_lines) > 1:
                ov_lines = []
                ov_len = 0
                for l in reversed(current_lines):
                    if ov_len + len(l) <= overlap:
                        ov_lines.insert(0, l)
                        ov_len += len(l)
                    else:
                        break
                current_lines = ov_lines
                current_len = ov_len
                start_line = max(1, idx - len(current_lines))
                char_start = current_char_offset - current_len
            else:
                current_lines = []
                current_len = 0
                start_line = idx
                char_start = current_char_offset

        current_lines.append(line)
        current_len += line_len
        current_char_offset += line_len

    if current_lines:
        c_text = "".join(current_lines).strip()
        if c_text:
            chunks.append(
                DocumentChunk(
                    chunk_index=len(chunks),
                    text=c_text,
                    heading_path="",
                    char_start=char_start,
                    char_end=char_start + len(c_text),
                    line_start=start_line,
                    line_end=len(lines),
                )
            )

    return chunks


def compute_sha256(path: Path) -> str:
    """Compute sha256 checksum with strict read-only file handling."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_default_roots() -> list[str]:
    """Retrieve configured document roots honoring user settings."""
    cfg = get_config()
    roots = getattr(cfg.memory, "roots", ["~/Documents/Obsidian Vault", "~/Documents/Study"])
    return [str(Path(r).expanduser().resolve()) for r in roots]


def get_ignored_roots() -> list[str]:
    """Retrieve explicitly ignored document roots (e.g. Certificates)."""
    cfg = get_config()
    ignored = getattr(cfg.memory, "ignored_roots", ["~/Documents/Certificates"])
    return [str(Path(r).expanduser().resolve()) for r in ignored]


def is_path_ignored(file_path: Path, ignored_roots: list[str]) -> bool:
    """Check if file path falls under any ignored roots or hidden directory."""
    str_path = str(file_path.resolve())
    for ign in ignored_roots:
        if str_path == ign or str_path.startswith(ign + os.sep):
            return True
    # Ignore any hidden component (.git, .obsidian, etc.)
    for part in file_path.parts:
        if part.startswith(".") and part not in (".", ".."):
            return True
    return False


async def index_documents(
    roots: list[str] | None = None,
    db: aiosqlite.Connection | None = None,
    force: bool = False,
) -> IndexingStats:
    """Incrementally index documents across configured roots (Read-Only Guarantee)."""
    start_time = datetime.now(UTC)
    active_roots = [str(Path(r).expanduser().resolve()) for r in (roots or get_default_roots())]
    ignored = get_ignored_roots()

    stats = IndexingStats(roots=active_roots)

    async def _execute_index(conn: aiosqlite.Connection) -> None:
        # Load existing indexed documents
        existing_rows = await fetch_all(conn, "SELECT id, path, mtime, size, sha256, source_root FROM documents")
        doc_map = {r["path"]: r for r in existing_rows}
        seen_paths: set[str] = set()

        for root_str in active_roots:
            root_path = Path(root_str)
            if not root_path.exists():
                log.info("Document root does not exist, skipping: %s", root_str)
                continue

            for dirpath, dirnames, filenames in os.walk(root_path):
                # Prune hidden directories in-place (.git, .obsidian, etc.)
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and not is_path_ignored(Path(dirpath) / d, ignored)]

                for fname in filenames:
                    if fname.startswith("."):
                        continue

                    fpath = Path(dirpath) / fname
                    if is_path_ignored(fpath, ignored):
                        continue

                    ext = fpath.suffix.lower()
                    if ext not in TEXT_EXTENSIONS:
                        continue

                    stats.scanned += 1
                    try:
                        st = fpath.stat()
                    except OSError:
                        continue

                    if st.st_size > MAX_FILE_SIZE_BYTES or st.st_size == 0:
                        continue

                    abs_path_str = str(fpath.resolve())
                    seen_paths.add(abs_path_str)

                    existing = doc_map.get(abs_path_str)

                    # Quick mtime + size check
                    if existing and not force:
                        if abs(float(existing["mtime"]) - st.st_mtime) < 0.001 and int(existing["size"]) == st.st_size:
                            stats.skipped += 1
                            continue

                    # Compute sha256
                    try:
                        file_sha = compute_sha256(fpath)
                    except OSError as err:
                        log.warning("Could not read file for indexing %s: %s", fpath, err)
                        continue

                    if existing and not force and existing["sha256"] == file_sha:
                        # Only mtime changed, update record
                        await execute(
                            conn,
                            "UPDATE documents SET mtime = ?, size = ? WHERE id = ?",
                            (st.st_mtime, st.st_size, existing["id"]),
                        )
                        stats.skipped += 1
                        continue

                    # Read content with strict read-only guarantee
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as rf:
                            content = rf.read()
                    except OSError as err:
                        log.warning("Failed to open file %s: %s", fpath, err)
                        continue

                    # Chunk document
                    if ext in (".md", ".markdown"):
                        doc_chunks = chunk_markdown(content)
                    else:
                        doc_chunks = chunk_generic_text(content)

                    now_iso = datetime.now(UTC).isoformat()
                    doc_id = existing["id"] if existing else f"doc_{uuid.uuid4().hex[:12]}"

                    if existing:
                        await execute(
                            conn,
                            """
                            UPDATE documents
                            SET mtime = ?, size = ?, sha256 = ?, indexed_at = ?
                            WHERE id = ?
                            """,
                            (st.st_mtime, st.st_size, file_sha, now_iso, doc_id),
                        )
                        await execute(conn, "DELETE FROM chunks WHERE document_id = ?", (doc_id,))
                        stats.updated += 1
                    else:
                        await execute(
                            conn,
                            """
                            INSERT INTO documents (id, path, mtime, size, sha256, indexed_at, source_root)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (doc_id, abs_path_str, st.st_mtime, st.st_size, file_sha, now_iso, root_str),
                        )
                        stats.indexed += 1

                    # Bulk insert chunks
                    for ch in doc_chunks:
                        ch_id = f"chk_{uuid.uuid4().hex[:12]}"
                        await execute(
                            conn,
                            """
                            INSERT INTO chunks (
                                id, document_id, chunk_index, text, heading_path,
                                char_start, char_end, line_start, line_end, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                ch_id,
                                doc_id,
                                ch.chunk_index,
                                ch.text,
                                ch.heading_path,
                                ch.char_start,
                                ch.char_end,
                                ch.line_start,
                                ch.line_end,
                                now_iso,
                            ),
                        )
                        stats.chunks_created += 1

        # Detect deleted documents for all configured roots
        for p_str, row in doc_map.items():
            if any(p_str.startswith(r) for r in active_roots):
                if p_str not in seen_paths and not Path(p_str).exists():
                    await execute(conn, "DELETE FROM documents WHERE id = ?", (row["id"],))
                    stats.deleted += 1

        await conn.commit()

    if db is not None:
        await _execute_index(db)
    else:
        async with get_db() as conn:
            await _execute_index(conn)

    duration = (datetime.now(UTC) - start_time).total_seconds()
    stats.duration_seconds = round(duration, 3)
    log.info(
        "Document indexing completed in %.3fs: %d indexed, %d updated, %d deleted, %d chunks",
        stats.duration_seconds,
        stats.indexed,
        stats.updated,
        stats.deleted,
        stats.chunks_created,
    )
    return stats


def extract_fts_doc_query(query: str, max_tokens: int = 12) -> str:
    """Build FTS5 match query from input string."""
    clean = re.sub(r"[^\w\s]", " ", query).strip()
    if not clean:
        return ""
    from noxai.memory.conversation import STOP_WORDS

    words = [w.lower() for w in clean.split() if len(w) > 1]
    content_words = [w for w in words if w not in STOP_WORDS]
    selected = content_words[:max_tokens] if content_words else words[:max_tokens]
    if not selected:
        return ""
    # Support prefix matching for short words
    terms = [f'"{w}"*' if len(w) > 3 else f'"{w}"' for w in selected]
    return " OR ".join(terms)


async def search_documents(
    query: str,
    k: int = 5,
    roots: list[str] | None = None,
    db: aiosqlite.Connection | None = None,
) -> list[ChunkMatch]:
    """Search document memory using FTS5 BM25 keyword matching."""
    fts_query = extract_fts_doc_query(query)
    if not fts_query:
        return []

    async def _run_search(conn: aiosqlite.Connection) -> list[ChunkMatch]:
        sql = """
            SELECT c.id, c.document_id, c.chunk_index, c.text, c.heading_path,
                   c.line_start, c.line_end, c.char_start, c.char_end,
                   d.path, d.source_root,
                   bm25(fts_document_chunks) as rank,
                   snippet(fts_document_chunks, 0, '<b>', '</b>', '...', 24) as fts_snippet
            FROM fts_document_chunks f
            JOIN chunks c ON f.rowid = c.rowid
            JOIN documents d ON c.document_id = d.id
            WHERE fts_document_chunks MATCH ?
            ORDER BY rank ASC
            LIMIT ?
        """
        rows = await fetch_all(conn, sql, (fts_query, k * 2))
        matches: list[ChunkMatch] = []
        for r in rows:
            if roots:
                if not any(r["source_root"] == root or r["path"].startswith(root) for root in roots):
                    continue
            matches.append(
                ChunkMatch(
                    document_id=r["document_id"],
                    path=r["path"],
                    source_root=r["source_root"],
                    heading_path=r["heading_path"],
                    text=r["text"],
                    snippet=r["fts_snippet"] or r["text"][:200],
                    score=float(r["rank"]),
                    line_start=int(r["line_start"]),
                    line_end=int(r["line_end"]),
                    char_start=int(r["char_start"]),
                    char_end=int(r["char_end"]),
                )
            )
            if len(matches) >= k:
                break
        return matches

    if db is not None:
        return await _run_search(db)
    async with get_db() as conn:
        return await _run_search(conn)


async def forget_document(path: str, db: aiosqlite.Connection | None = None) -> bool:
    """Remove a document and its chunks from memory index."""
    abs_path = str(Path(path).expanduser().resolve())

    async def _forget(conn: aiosqlite.Connection) -> bool:
        row = await fetch_one(conn, "SELECT id FROM documents WHERE path = ?", (abs_path,))
        if not row:
            return False
        await execute(conn, "DELETE FROM documents WHERE id = ?", (row["id"],))
        await conn.commit()
        return True

    if db is not None:
        return await _forget(db)
    async with get_db() as conn:
        return await _forget(conn)


async def get_memory_stats(db: aiosqlite.Connection | None = None) -> dict[str, Any]:
    """Return document memory statistics (document count, chunk count, roots)."""

    async def _stats(conn: aiosqlite.Connection) -> dict[str, Any]:
        doc_cnt_row = await fetch_one(conn, "SELECT COUNT(*) as c FROM documents")
        chunk_cnt_row = await fetch_one(conn, "SELECT COUNT(*) as c FROM chunks")
        roots_rows = await fetch_all(conn, "SELECT DISTINCT source_root FROM documents")

        doc_count = int(doc_cnt_row["c"]) if doc_cnt_row else 0
        chunk_count = int(chunk_cnt_row["c"]) if chunk_cnt_row else 0
        active_roots = [r["source_root"] for r in roots_rows] if roots_rows else get_default_roots()

        # Database disk size estimate
        db_path = Path(os.environ.get("NOX_DATABASE_PATH", "~/.local/share/nox/nox.db")).expanduser().resolve()
        db_size_bytes = db_path.stat().st_size if db_path.exists() else 0

        # Last indexed time
        last_row = await fetch_one(conn, "SELECT MAX(indexed_at) as last_idx FROM documents")
        last_indexed = last_row["last_idx"] if last_row and last_row["last_idx"] else None

        return {
            "document_count": doc_count,
            "chunk_count": chunk_count,
            "configured_roots": get_default_roots(),
            "indexed_roots": active_roots,
            "ignored_roots": get_ignored_roots(),
            "database_size_bytes": db_size_bytes,
            "database_size_mb": round(db_size_bytes / (1024 * 1024), 2),
            "last_indexed_at": last_indexed,
        }

    if db is not None:
        return await _stats(db)
    async with get_db() as conn:
        return await _stats(conn)
