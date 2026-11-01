"""Phase 5 Tests: Notes Vault (Obsidian-compatible Markdown Vault)."""

from __future__ import annotations

import os
from pathlib import Path
import time
import pytest
import frontmatter

from noxai.db import fetch_all, fetch_one, get_db, init_db
from noxai.notes.vault import NotesVault, BEGIN_MARKER, END_MARKER


@pytest.fixture
async def setup_vault(tmp_path, monkeypatch):
    test_db = tmp_path / "test_vault.db"
    monkeypatch.setattr("noxai.db.DB_PATH", test_db)
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    await init_db()

    vault_dir = tmp_path / "NoX-Vault"
    vault = NotesVault(vault_path=vault_dir)
    return vault


def test_vault_structure_initialization(setup_vault):
    """Verify standard folder layout and _nox system files are created."""
    vault = setup_vault
    root = vault.vault_path

    assert (root / "Briefings").is_dir()
    assert (root / "Items/News").is_dir()
    assert (root / "Items/Opportunities").is_dir()
    assert (root / "Items/Ideas").is_dir()
    assert (root / "Topics").is_dir()
    assert (root / "Dashboards").is_dir()
    assert (root / "Threads").is_dir()
    assert (root / "_nox/README.md").is_file()
    assert (root / "_nox/schema.md").is_file()


def test_path_traversal_rejected(setup_vault):
    """Ensure path traversal attempts outside the vault raise ValueError."""
    vault = setup_vault

    with pytest.raises(ValueError, match="Path traversal rejected"):
        vault.write_file_atomic("../outside.md", "Malicious content")

    with pytest.raises(ValueError, match="Path traversal rejected"):
        vault.write_file_atomic("/etc/passwd", "Malicious content")


def test_user_text_outside_markers_survives(setup_vault):
    """Verify user notes outside %% nox:begin %% and %% nox:end %% are preserved."""
    vault = setup_vault
    rel_path = "Items/News/test-article--it_01.md"

    # Step 1: Initial automated export
    initial_content = "### What happened\nCritical Linux bug found.\n\n### Why it matters\nKernel patch needed."
    vault.write_file_atomic(rel_path, initial_content, {"nox_id": "it_01", "type": "news", "status": "new"})

    target = vault.vault_path / rel_path
    assert target.exists()

    # Step 2: Simulate user editing the note in Obsidian (adding personal notes above and below)
    raw_text = target.read_text(encoding="utf-8")
    user_header = "# My Personal Review\nNeed to inform the infrastructure team ASAP.\n\n"
    user_footer = "\n\n## Action Items\n- [ ] Check production servers\n- [ ] Notify CISO\n"

    # Insert user notes outside markers
    post = frontmatter.loads(raw_text)
    post.content = f"{user_header}{post.content}{user_footer}"
    target.write_text(frontmatter.dumps(post), encoding="utf-8")

    # Step 3: NoX re-exports an updated version of the item
    updated_nox_content = "### What happened\nCritical Linux bug updated with CVE-2026-9999.\n\n### Why it matters\nExploit in the wild."
    vault.write_file_atomic(rel_path, updated_nox_content, {"nox_id": "it_01", "type": "news", "status": "new"})

    # Step 4: Verify that user notes survived completely
    final_post = frontmatter.loads(target.read_text(encoding="utf-8"))
    content = final_post.content

    assert "My Personal Review" in content
    assert "Check production servers" in content
    assert "Notify CISO" in content
    assert "CVE-2026-9999" in content
    assert BEGIN_MARKER in content
    assert END_MARKER in content


def test_idempotent_rerun_produces_no_diff(setup_vault):
    """Verify that re-running export with identical data produces identical files."""
    vault = setup_vault
    item = {
        "item_id": "it_quantum",
        "title": "Quantum Coherence Milestone",
        "summary": "1000 qubits cohered for 10 minutes.",
        "why_it_matters": "Major cryptographic implications.",
        "source_name": "MIT Technology Review",
        "url": "https://technologyreview.com/quantum",
        "category": "news",
        "topic_name": "Quantum Computing",
    }

    path1 = vault.export_item_note(item, "2026-10-31", "morning")
    content1 = path1.read_text(encoding="utf-8")

    path2 = vault.export_item_note(item, "2026-10-31", "morning")
    content2 = path2.read_text(encoding="utf-8")

    assert content1 == content2


@pytest.mark.asyncio
async def test_vault_readback_and_feedback_sync(setup_vault):
    """Verify that setting rating: like in note frontmatter syncs back to DB."""
    vault = setup_vault
    item = {
        "item_id": "it_k8s_cve",
        "title": "Kubernetes Ingress Bypass",
        "summary": "High severity security advisory issued.",
        "source_name": "The Hacker News",
        "url": "https://thehackernews.com/k8s",
        "category": "news",
        "topic_name": "Cybersecurity",
    }
    note_path = vault.export_item_note(item, "2026-10-31", "morning")

    # User opens note in Obsidian and marks rating: like, status: saved
    post = frontmatter.loads(note_path.read_text(encoding="utf-8"))
    post.metadata["rating"] = "like"
    post.metadata["status"] = "saved"
    note_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    # Run sync
    stats = await vault.sync_vault_to_db()
    assert stats["synced"] >= 1

    # Verify DB recorded the feedback
    async with get_db() as db:
        fb_row = await fetch_one(
            db,
            "SELECT * FROM briefing_feedback WHERE item_id = ?",
            ("it_k8s_cve",),
        )
        assert fb_row is not None
        assert fb_row["rating"] == "like"
        assert "Notes Vault" in fb_row["reason_text"]


def test_dashboard_regeneration_performance_with_500_notes(setup_vault):
    """Verify dashboard generation across 500 notes completes in under 5.0 seconds."""
    vault = setup_vault
    items_dir = vault.vault_path / "Items/Opportunities"

    # Pre-generate 500 opportunity notes
    for i in range(500):
        opp_meta = {
            "nox_id": f"opp_{i:04d}",
            "type": "opportunity",
            "topic": "AI Competitions" if i % 2 == 0 else "Hackathons",
            "title": f"Global AI Challenge {i}",
            "deadline": f"2026-11-{(i % 28) + 1:02d}",
            "prize": f"${(i + 1) * 1000}",
            "mode": "Online",
            "status": "new" if i % 3 == 0 else "saved",
        }
        post = frontmatter.Post(
            f"%% nox:begin %%\n# Challenge {i}\nDetails.\n%% nox:end %%\n",
            **opp_meta,
        )
        file_path = items_dir / f"challenge-{i:04d}--opp_{i:04d}.md"
        file_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    t0 = time.perf_counter()
    vault.regenerate_dashboards()
    duration = time.perf_counter() - t0

    print(f"\nDashboard regeneration for 500 notes took: {duration:.3f}s")
    assert duration < 5.0

    opp_board = (vault.vault_path / "Dashboards/Opportunities Board.md").read_text(encoding="utf-8")
    assert "Global AI Challenge 0" in opp_board
    assert "Global AI Challenge 499" in opp_board
    assert "```dataview" in opp_board
