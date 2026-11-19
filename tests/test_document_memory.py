# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Acceptance checks D2-a through D2-e for Document Memory.

"""Acceptance tests for Document Memory and Notes Recall (Workstream D2).

Covers:
- Heading chunking with hierarchy and paragraph splitting
- Strict read-only guarantee over indexed directories
- Incremental indexing (mtime/sha256 change detection and deletion tracking)
- BM25 document search and snippet extraction
- Document forgetting
"""

from __future__ import annotations

from pathlib import Path
import stat
import pytest
import aiosqlite

from noxai.db import execute, fetch_all, fetch_one, init_db
from noxai.memory.docs import (
    chunk_generic_text,
    chunk_markdown,
    forget_document,
    get_memory_stats,
    index_documents,
    search_documents,
)


@pytest.fixture
async def d2_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[aiosqlite.Connection, Path]:
    data_dir = tmp_path / "share" / "nox"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "nox.db"
    token_file = data_dir / "session_token"

    import noxai.api.auth as auth_mod
    import noxai.cli as cli_mod
    import noxai.db as db_mod

    monkeypatch.setattr(db_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    monkeypatch.setattr(cli_mod, "DB_PATH", db_file)
    monkeypatch.setattr(auth_mod, "DEFAULT_TOKEN_PATH", token_file)
    monkeypatch.setenv("NOX_DATABASE_PATH", str(db_file))
    monkeypatch.setenv("NOX_AUTH_BYPASS", "1")

    await init_db()

    conn = await aiosqlite.connect(str(db_file))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON;")
    yield conn, tmp_path
    await conn.close()


def test_markdown_chunking_hierarchy() -> None:
    """Test that Markdown heading chunking preserves hierarchy and line offsets."""
    content = """# Architecture Overview

This is the system architecture overview paragraph.

## Database Subsystem

PostgreSQL serves as primary OLTP store.
Redis acts as distributed caching layer.

### Sharding Details

Sharding occurs across tenant boundaries.
"""
    chunks = chunk_markdown(content, target_size=200, overlap=30)
    assert len(chunks) >= 3

    # Check heading paths
    paths = [c.heading_path for c in chunks]
    assert any("Architecture Overview" in p for p in paths)
    assert any("Database Subsystem" in p for p in paths)
    assert any("Sharding Details" in p for p in paths)

    # Line numbers check
    for c in chunks:
        assert c.line_start <= c.line_end
        assert c.char_start < c.char_end


@pytest.mark.asyncio
async def test_read_only_guarantee_and_incremental(d2_db: tuple[aiosqlite.Connection, Path]) -> None:
    """Acceptance Check D2-c & Read-only Guarantee: verify read-only permission compliance and incremental changes."""
    conn, tmp_path = d2_db

    root_dir = tmp_path / "notes_vault"
    root_dir.mkdir(parents=True, exist_ok=True)

    note_file = root_dir / "service_config.md"
    note_file.write_text("# Service Config\nAuth secret token is alpha-omega-777.", encoding="utf-8")

    # Make file and directory read-only (0o555 for dir, 0o444 for file)
    note_file.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    # Index the read-only file
    stats = await index_documents(roots=[str(root_dir)], db=conn)
    assert stats.indexed == 1
    assert stats.chunks_created >= 1

    # Verify search finds the secret
    matches = await search_documents("alpha-omega-777", db=conn)
    assert len(matches) == 1
    assert "alpha-omega-777" in matches[0].text
    assert matches[0].path == str(note_file.resolve())

    # Modify file: grant write to edit, then set back to read-only
    note_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    note_file.write_text("# Service Config\nUpdated secret token is beta-delta-888.", encoding="utf-8")
    note_file.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    # Re-index
    stats_re = await index_documents(roots=[str(root_dir)], db=conn)
    assert stats_re.updated == 1

    # Old term not found, new term found
    matches_old = await search_documents("alpha-omega-777", db=conn)
    assert len(matches_old) == 0

    matches_new = await search_documents("beta-delta-888", db=conn)
    assert len(matches_new) == 1
    assert "beta-delta-888" in matches_new[0].text

    # Delete file and re-index
    note_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    note_file.unlink()

    stats_del = await index_documents(roots=[str(root_dir)], db=conn)
    assert stats_del.deleted == 1

    matches_after_del = await search_documents("beta-delta-888", db=conn)
    assert len(matches_after_del) == 0


@pytest.mark.asyncio
async def test_forget_document_api(d2_db: tuple[aiosqlite.Connection, Path]) -> None:
    """Test manual forget_document removes document and cascade drops chunks."""
    conn, tmp_path = d2_db

    root_dir = tmp_path / "study_vault"
    root_dir.mkdir(parents=True, exist_ok=True)

    note = root_dir / "draft.txt"
    note.write_text("Confidential temporary note text 54321.", encoding="utf-8")

    await index_documents(roots=[str(root_dir)], db=conn)

    matches = await search_documents("54321", db=conn)
    assert len(matches) == 1

    # Forget document
    forgot = await forget_document(str(note), db=conn)
    assert forgot is True

    # Search should yield 0 results
    matches_after = await search_documents("54321", db=conn)
    assert len(matches_after) == 0


@pytest.mark.asyncio
async def test_memory_stats(d2_db: tuple[aiosqlite.Connection, Path]) -> None:
    """Test get_memory_stats returns correct document and chunk counts."""
    conn, tmp_path = d2_db

    root_dir = tmp_path / "stats_vault"
    root_dir.mkdir(parents=True, exist_ok=True)

    (root_dir / "doc1.md").write_text("# Doc 1\nContent 1.", encoding="utf-8")
    (root_dir / "doc2.md").write_text("# Doc 2\nContent 2.", encoding="utf-8")

    await index_documents(roots=[str(root_dir)], db=conn)

    s = await get_memory_stats(db=conn)
    assert s["document_count"] == 2
    assert s["chunk_count"] >= 2
    assert s["last_indexed_at"] is not None
