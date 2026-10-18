"""Skills loading, front-matter parsing, and embedding indexing per Section 6.8."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noxai.db import execute, fetch_all

if TYPE_CHECKING:
    import aiosqlite

    from noxai.models.router import ModelRouter

log = logging.getLogger(__name__)

DEFAULT_SKILLS_DIR = Path(os.path.expanduser("~/.local/share/nox/skills"))

BUILTIN_SKILLS: dict[str, str] = {
    "briefing-style.md": (
        "---\n"
        "name: briefing-style\n"
        "description: Guidelines for formatting morning news briefings and executive digests\n"
        "---\n\n"
        "# Briefing Style Guidelines\n"
        "- Start with a concise 3-line TL;DR summarizing the most significant developments.\n"
        "- Group items by thematic categories (e.g. Cybersecurity, AI/Tech, Regional Ecosystem).\n"
        "- Limit each category to the top 4-6 highest-impact items.\n"
        "- Format each item with:\n"
        "  - **Headline**: Clear and direct\n"
        "  - **Summary**: 2 concise sentences explaining what happened\n"
        "  - **Why it matters**: 1 line contextualizing relevance for a cybersecurity student\n"
        "  - **Source**: Explicit bracketed Markdown link to primary reference\n"
        "- If a category had no new updates, state 'No significant updates today' in one line.\n"
    ),
    "research-method.md": (
        "---\n"
        "name: research-method\n"
        "description: Verification methodology, cross-checking claims, and source ranking\n"
        "---\n\n"
        "# Research Methodology Guidelines\n"
        "- Prefer primary sources (RFCs, official advisories, CVEs, commits) over blogs.\n"
        "- Cross-check critical claims against at least two independent sources.\n"
        "- When conflicting perspectives exist, explicitly present both viewpoints.\n"
        "- Report gaps, ambiguous data, or unverified claims in a dedicated Gaps section.\n"
        "- Never extrapolate or invent technical details not grounded in fetched evidence.\n"
    ),
}


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Extract YAML-like frontmatter between --- fences and the remaining markdown body."""
    meta: dict[str, str] = {}
    content = content.strip()
    if not content.startswith("---"):
        return meta, content

    parts = re.split(r"^---\s*$", content, maxsplit=2, flags=re.MULTILINE)
    if len(parts) >= 3:
        fm_text = parts[1]
        body = parts[2].strip()
        for line in fm_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, val = line.split(":", 1)
                meta[key.strip()] = val.strip().strip("\"'")
        return meta, body

    return meta, content


def ensure_builtin_skills(skills_dir: Path = DEFAULT_SKILLS_DIR) -> None:
    """Create default skill files if they do not already exist on disk."""
    skills_dir.mkdir(parents=True, exist_ok=True)
    for filename, text in BUILTIN_SKILLS.items():
        skill_path = skills_dir / filename
        if not skill_path.exists():
            skill_path.write_text(text, encoding="utf-8")
            log.info("Created built-in skill: %s", skill_path)


async def index_skills(
    skills_dir: Path = DEFAULT_SKILLS_DIR,
    db: aiosqlite.Connection | None = None,
    router: ModelRouter | None = None,
) -> int:
    """Scan skills directory, read frontmatter, and update skills_index table."""
    if not skills_dir.exists():
        ensure_builtin_skills(skills_dir)

    indexed = 0
    md_files = list(skills_dir.glob("*.md"))

    for path in md_files:
        try:
            content = path.read_text(encoding="utf-8")
            meta, _body = parse_frontmatter(content)
            name = meta.get("name") or path.stem
            description = meta.get("description") or f"Skill from {path.name}"

            embedding_bytes: bytes | None = None
            if router is not None:
                try:
                    embed_res = await router.embed(
                        role="embed",
                        texts=[f"{name}: {description}"],
                    )
                    if embed_res:
                        import numpy as np

                        embedding_bytes = np.array(embed_res[0], dtype=np.float32).tobytes()
                except Exception as exc:
                    log.debug("Embedding skill '%s' skipped: %s", name, exc)

            if db is not None:
                await execute(
                    db,
                    """
                    INSERT INTO skills_index (name, description, filename, embedding, updated_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(name) DO UPDATE SET
                        description = excluded.description,
                        filename = excluded.filename,
                        embedding = coalesce(excluded.embedding, skills_index.embedding),
                        updated_at = datetime('now')
                    """,
                    (name, description, path.name, embedding_bytes),
                )
            indexed += 1
        except Exception as exc:
            log.warning("Failed indexing skill file '%s': %s", path, exc)

    if db is not None:
        await db.commit()
    return indexed


async def find_relevant_skills(
    query: str,
    db: aiosqlite.Connection,
    skills_dir: Path = DEFAULT_SKILLS_DIR,
    top_k: int = 2,
) -> list[dict[str, Any]]:
    """Retrieve skill bodies relevant to query based on keyword and name matching."""
    rows = await fetch_all(
        db,
        "SELECT name, description, filename FROM skills_index ORDER BY id ASC",
    )
    if not rows:
        return []

    q_lower = query.lower()
    matches: list[dict[str, Any]] = []

    for r in rows:
        s_name = str(r["name"])
        s_desc = str(r["description"])
        s_file = str(r["filename"])

        # Relevance scoring heuristic
        score = 0
        if s_name.lower() in q_lower:
            score += 10
        if any(word in q_lower for word in s_name.lower().split("-")):
            score += 5
        if any(w in s_desc.lower() for w in q_lower.split() if len(w) > 3):
            score += 3

        if score > 0 or len(matches) < top_k:
            # Read body from disk
            skill_path = skills_dir / s_file
            body = ""
            if skill_path.exists():
                _, body = parse_frontmatter(skill_path.read_text(encoding="utf-8"))
            matches.append(
                {
                    "name": s_name,
                    "description": s_desc,
                    "body": body,
                    "score": score,
                }
            )

    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches[:top_k]
