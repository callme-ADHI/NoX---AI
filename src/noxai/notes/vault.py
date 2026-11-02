"""NoX Notes Vault: Obsidian-compatible structured Markdown vault.

Provides plain Markdown export with YAML frontmatter, [[wikilinks]],
atomic file writes, %% nox:begin %% / %% nox:end %% preservation of user edits,
path traversal guards, dashboard generation, and bidirectional DB sync.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiosqlite
import frontmatter

from noxai.config import Settings, get_config
from noxai.db import execute, fetch_one, get_db

log = logging.getLogger(__name__)

BEGIN_MARKER = "%% nox:begin %%"
END_MARKER = "%% nox:end %%"


class NotesVault:
    """Manages reading and writing notes in the NoX Notes Vault."""

    def __init__(
        self,
        vault_path: Path | str | None = None,
        config: Settings | None = None,
    ) -> None:
        self.config = config or get_config()
        if vault_path:
            self.vault_path = Path(vault_path).expanduser().resolve()
        else:
            self.vault_path = Path(self.config.notes.vault_path).expanduser().resolve()
        self.ensure_structure()

    def ensure_structure(self) -> None:
        """Create standard folder layout and _nox system files."""
        dirs = [
            "Briefings",
            "Items/News",
            "Items/Opportunities",
            "Items/Internships",
            "Items/Hackathons",
            "Items/Ideas",
            "Topics",
            "Dashboards",
            "Threads",
            "_nox/templates",
        ]
        for d in dirs:
            (self.vault_path / d).mkdir(parents=True, exist_ok=True)

        readme = self.vault_path / "_nox" / "README.md"
        if not readme.exists():
            readme.write_text(
                "# NoX Vault\n\n"
                "Autonomous intelligence briefings, items, and threads recorded by NoX.\n"
                "Content between `%% nox:begin %%` and `%% nox:end %%` is maintained by NoX.\n"
                "Any user notes written outside these markers are permanently preserved.\n",
                encoding="utf-8",
            )

        schema = self.vault_path / "_nox" / "schema.md"
        if not schema.exists():
            schema.write_text(
                "# Notes Vault Schema\n\n"
                "- Item frontmatter: `nox_id`, `type`, `topic`, `tags`, `published`, `fetched`,\n"
                "  `source`, `url`, `link_status`, `status` [new|read|saved|applied|dismissed],\n"
                "  `rating` [like|dislike], `briefing` (wikilink).\n"
                "- User text: Write anything outside `%% nox:begin %%` and `%% nox:end %%`.\n",
                encoding="utf-8",
            )

    def _safe_path(self, rel_path: str | Path) -> Path:
        """Resolve path and enforce that it remains inside the vault root."""
        norm = Path(rel_path)
        if norm.is_absolute():
            # Must still reside within vault_path
            resolved = norm.resolve()
        else:
            resolved = (self.vault_path / norm).resolve()

        try:
            resolved.relative_to(self.vault_path)
        except ValueError as exc:
            raise ValueError(
                f"Path traversal rejected: '{rel_path}' resolves outside vault '{self.vault_path}'"
            ) from exc
        return resolved

    @staticmethod
    def _slugify(text: str, max_len: int = 50) -> str:
        """Convert string to kebab-case filename slug."""
        clean = re.sub(r"[^\w\s-]", "", text).strip().lower()
        slug = re.sub(r"[-\s]+", "-", clean)[:max_len].strip("-")
        return slug or "item"

    def write_file_atomic(
        self,
        rel_path: str | Path,
        content: str,
        frontmatter_dict: dict[str, Any] | None = None,
    ) -> Path:
        """Write file atomically, preserving user edits outside nox markers and user frontmatter."""
        target = self._safe_path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        meta: dict[str, Any] = dict(frontmatter_dict or {})
        nox_body = content.strip()

        if target.exists():
            try:
                existing_text = target.read_text(encoding="utf-8")
                post = frontmatter.loads(existing_text)

                # Preserve user modifications to frontmatter status / rating
                if post.metadata.get("status") in ("read", "saved", "applied", "dismissed"):
                    meta["status"] = post.metadata["status"]
                if post.metadata.get("rating") in ("like", "dislike"):
                    meta["rating"] = post.metadata["rating"]

                existing_body = post.content
                if BEGIN_MARKER in existing_body and END_MARKER in existing_body:
                    b_idx = existing_body.index(BEGIN_MARKER)
                    e_idx = existing_body.index(END_MARKER) + len(END_MARKER)
                    prefix = existing_body[:b_idx]
                    suffix = existing_body[e_idx:]
                    final_body = f"{prefix}{BEGIN_MARKER}\n{nox_body}\n{END_MARKER}{suffix}"
                else:
                    # User content exists but markers were absent -> preserve user text below
                    final_body = (
                        f"{BEGIN_MARKER}\n{nox_body}\n{END_MARKER}\n\n{existing_body.strip()}\n"
                    )
            except Exception as exc:
                log.warning("Error reading existing note at %s: %s; rewriting", target, exc)
                final_body = f"{BEGIN_MARKER}\n{nox_body}\n{END_MARKER}\n"
        else:
            final_body = f"{BEGIN_MARKER}\n{nox_body}\n{END_MARKER}\n"

        out_post = frontmatter.Post(final_body, **meta)
        serialized = frontmatter.dumps(out_post) + "\n"

        # Atomic write via temp file in same directory
        temp_name = f".{target.name}.tmp.{uuid.uuid4().hex[:8]}"
        temp_path = target.parent / temp_name
        try:
            temp_path.write_text(serialized, encoding="utf-8")
            os.replace(temp_path, target)
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

        return target

    def export_item_note(self, item: dict[str, Any], date_str: str, slot: str) -> Path:
        """Export an item note to Items/{News|Opportunities|Internships|Hackathons|Ideas}/."""
        nox_id = item.get("item_id") or item.get("id") or f"it_{uuid.uuid4().hex[:8]}"
        title = item.get("title", "Untitled Item")
        slug = self._slugify(title)
        category = (item.get("category") or "news").lower()
        topic_name = item.get("topic_name") or item.get("topic_id") or "General"

        if category in ("internship", "internships"):
            itype = "internship"
            folder = "Internships"
        elif category in ("hackathon", "hackathons"):
            itype = "hackathon"
            folder = "Hackathons"
        elif category in ("opportunities", "competitions", "grants", "bounties", "ctfs"):
            itype = "opportunity"
            folder = "Opportunities"
        elif category in ("ideas", "startup_ideas"):
            itype = "idea"
            folder = "Ideas"
        else:
            itype = "news"
            folder = "News"

        rel_path = f"Items/{folder}/{slug}--{nox_id}.md"

        # Determine real link status
        if "link_status" in item and item["link_status"] != "unknown":
            real_link_status = item["link_status"]
        elif item.get("verified_link") is True:
            real_link_status = "verified"
        elif item.get("verified_link") is False:
            real_link_status = "unverified"
        else:
            real_link_status = "unknown"

        meta: dict[str, Any] = {
            "title": title,
            "nox_id": nox_id,
            "type": itype,
            "topic": topic_name,
            "tags": [itype, self._slugify(topic_name)],
            "published": item.get("published_at") or date_str,
            "fetched": date_str,
            "source": item.get("source_name")
            or item.get("organisation")
            or item.get("organiser")
            or "Unknown",
            "url": item.get("url") or item.get("apply_url") or "",
            "link_status": real_link_status,
            "status": item.get("status") or "new",
            "rating": None,
            "briefing": f"[[{date_str}-{slot}]]",
        }

        if itype == "internship":
            meta.update(
                {
                    "org": item.get("organisation") or item.get("org") or "Unknown",
                    "country": item.get("country", "unknown"),
                    "city": item.get("city", ""),
                    "mode": item.get("mode", "unknown"),
                    "fields": item.get("fields", []),
                    "deadline": item.get("deadline", "unknown"),
                    "score": item.get("score", 0),
                    "benefits": item.get("benefits", {}),
                    "eligibility_match": item.get("eligibility_match", "unknown"),
                    "apply_url": item.get("apply_url") or item.get("url", ""),
                    "source_url": item.get("source_url") or item.get("url", ""),
                    "evidence": item.get("evidence", {}),
                    "why_ranked": item.get("why_ranked", []),
                }
            )
        elif itype == "hackathon":
            meta.update(
                {
                    "org": item.get("organiser") or item.get("org") or "Unknown",
                    "country": item.get("city_or_country") or item.get("country", "unknown"),
                    "city": item.get("city", ""),
                    "mode": item.get("mode", "unknown"),
                    "themes": item.get("themes", []),
                    "deadline": item.get("deadline", "unknown"),
                    "score": item.get("score", 0),
                    "prize": item.get("prize_text") or item.get("prize", "unknown"),
                    "fee": item.get("fee", "free"),
                    "travel_accommodation": item.get("travel_or_accommodation_covered", "unknown"),
                    "apply_url": item.get("apply_url") or item.get("url", ""),
                    "source_url": item.get("source_url") or item.get("url", ""),
                    "why_ranked": item.get("why_ranked", []),
                }
            )
        elif itype == "opportunity":
            meta.update(
                {
                    "deadline": item.get("deadline", "unknown"),
                    "event_start": item.get("event_start", "unknown"),
                    "mode": item.get("mode", "unknown"),
                    "location": item.get("location", "unknown"),
                    "eligibility": item.get("eligibility", "unknown"),
                    "prize": item.get("prize", "unknown"),
                    "registration_url": item.get("registration_url") or item.get("url", ""),
                }
            )

        why_it_matters_text = item.get("why_it_matters") or item.get("takeaway") or ""
        body_lines = [
            f"# {title}",
            "",
            "### What happened",
            f"{item.get('summary', title)}",
            "",
            "### Why it matters",
            why_it_matters_text if why_it_matters_text else f"{item.get('summary', title)}",
            "",
            "### Key details",
            f"- **Topic**: {topic_name}",
            f"- **Source**: {meta.get('source')}",
            f"- **Relevance / Value Score**: {item.get('score', item.get('relevance_score', 1.0))}",
            "",
            "### Sources",
            f"- [{meta.get('source')}]({meta.get('url')}) ({real_link_status} link)",
        ]

        if itype == "internship":
            body_lines.extend(
                [
                    "",
                    "### Benefits & Eligibility Evidence",
                    "| Benefit | Status | Evidence from Source |",
                    "|---|---|---|",
                ]
            )
            b_data = item.get("benefits", {})
            if hasattr(b_data, "model_dump"):
                b_data = b_data.model_dump()
            ev_data = item.get("evidence", {})
            for b_key in ("accommodation", "stipend", "travel", "visa_support"):
                b_val = b_data.get(b_key, "unknown") if isinstance(b_data, dict) else "unknown"
                b_ev = ev_data.get(b_key, "") if isinstance(ev_data, dict) else ""
                body_lines.append(f"| {b_key.capitalize()} | {b_val} | {b_ev or 'None'} |")
            body_lines.extend(
                [
                    "",
                    "### How to apply",
                    f"[{meta.get('apply_url')}]({meta.get('apply_url')})",
                ]
            )
        elif itype == "hackathon":
            body_lines.extend(
                [
                    "",
                    "### Hackathon details",
                    "| Metric | Details |",
                    "|---|---|",
                    f"| Deadline | {meta.get('deadline')} |",
                    f"| Prize | {meta.get('prize')} |",
                    f"| Mode | {meta.get('mode')} |",
                    f"| Location | {meta.get('country')} |",
                    f"| Fee | {meta.get('fee')} |",
                    f"| Travel / Stay Support | {meta.get('travel_accommodation')} |",
                    "",
                    "### How to apply",
                    f"[{meta.get('apply_url')}]({meta.get('apply_url')})",
                ]
            )
        elif itype == "opportunity":
            body_lines.extend(
                [
                    "",
                    "### Key facts",
                    "| Metric | Details |",
                    "|---|---|",
                    f"| Deadline | {meta.get('deadline')} |",
                    f"| Prize | {meta.get('prize')} |",
                    f"| Mode | {meta.get('mode')} |",
                    f"| Location | {meta.get('location')} |",
                    f"| Eligibility | {meta.get('eligibility')} |",
                    "",
                    "### How to apply",
                    f"[{meta.get('registration_url')}]({meta.get('registration_url')})",
                ]
            )

        return self.write_file_atomic(rel_path, "\n".join(body_lines), meta)

    def export_digest_note(
        self,
        date_str: str,
        slot: str,
        title: str,
        analyst_data: dict[str, Any],
        generation_seconds: float,
    ) -> Path:
        """Export BLUF style briefing digest note."""
        year, month, _ = date_str.split("-")
        rel_path = f"Briefings/{year}/{month}/{date_str}-{slot}.md"

        items = analyst_data.get("items", [])
        top_3 = analyst_data.get("top_3", [])
        tldr = analyst_data.get("tldr", "")

        meta = {
            "title": f"NoX Briefing — {date_str} {slot.capitalize()}",
            "date": date_str,
            "slot": slot,
            "tags": ["briefing", slot],
            "items_count": len(items),
            "generation_seconds": round(generation_seconds, 2),
        }

        lines: list[str] = [
            f"# {title}",
            "",
            f"**Generated**: {date_str} {slot.capitalize()} | "
            f"**Indexed**: {len(items)} verified items | "
            f"**Speed**: {generation_seconds:.1f}s",
            "",
        ]

        # Separate items by category
        internships = [
            it
            for it in items
            if (it.get("category") or "").lower() in ("internship", "internships")
        ]
        hackathons = [
            it for it in items if (it.get("category") or "").lower() in ("hackathon", "hackathons")
        ]
        competitions = [
            it
            for it in items
            if (it.get("category") or "").lower() in ("competitions", "opportunities", "ctfs")
        ]
        ideas = [
            it for it in items if (it.get("category") or "").lower() in ("ideas", "startup_ideas")
        ]
        news_items = [
            it
            for it in items
            if (it.get("category") or "").lower()
            not in (
                "internship",
                "internships",
                "hackathon",
                "hackathons",
                "competitions",
                "opportunities",
                "ctfs",
                "ideas",
                "startup_ideas",
            )
        ]

        if slot == "evening":
            lines.extend(
                [
                    "> [!note] Since this morning",
                    "> New intelligence and afternoon releases.",
                    "",
                ]
            )
            # Check deadlines closing within 7 days
            urgent_opps = [
                op
                for op in (internships + hackathons + competitions)
                if any("[urgent]" in str(r) for r in op.get("why_ranked", []))
            ]
            if urgent_opps:
                lines.append("> [!warning] Deadlines closing within 7 days")
                for u in urgent_opps[:5]:
                    apply_link = u.get("url") or u.get("apply_url")
                    lines.append(
                        f"> - **{u.get('title')}**: Deadline {u.get('deadline', 'soon')} "
                        f"([apply]({apply_link}))"
                    )
                lines.append("")

        if analyst_data.get("is_degraded"):
            lines.append("> [!warning] Mode: Degraded (Local execution only — Cloud disabled)\n")

        lines.extend(
            [
                "> [!summary] TL;DR",
            ]
        )
        for t_line in tldr.split("\n"):
            if t_line.strip():
                lines.append(f"> - {t_line.strip()}")
        lines.append("")

        lines.append("## ⭐ Top Picks")
        for it in top_3:
            slug = self._slugify(it.get("title", ""))
            nox_id = it.get("item_id", "")
            cat_label = it.get("category", "Pick").capitalize()
            lines.append(
                f"> [!important] [{cat_label}] [[{slug}--{nox_id}|{it.get('title', '')}]]\n"
                f"> {it.get('summary', '')}\n"
                f"> [Original Source]({it.get('url') or it.get('apply_url', '')})"
            )
            lines.append("")

        # 1. Funded Internships
        if internships:
            target_int = 10
            found_int = len(internships)
            int_subtitle = f"*(only {found_int} found — target: {target_int})*" if found_int < target_int else f"*({found_int} verified)*"
            lines.append(f"## 🎓 Funded Internships & Programs {int_subtitle}")
            lines.append(
                "| Program | Organisation | Location | Deadline | "
                "Score | Housing & Stipend | Apply |"
            )
            lines.append("|---|---|---|---|---|---|---|")
            for it in internships[:10]:
                slug = self._slugify(it.get("title", ""))
                nox_id = it.get("item_id", "")
                b = it.get("benefits", {})
                if hasattr(b, "model_dump"):
                    b = b.model_dump()
                b_str = (
                    f"Housing: {b.get('accommodation', 'unknown')} | "
                    f"Stipend: {b.get('stipend', 'unknown')}"
                )
                lines.append(
                    f"| [[{slug}--{nox_id}|{it.get('title', '')}]] | "
                    f"{it.get('organisation') or it.get('org') or 'Unknown'} | "
                    f"{it.get('country', 'unknown')} | "
                    f"{it.get('deadline', 'unknown')} | "
                    f"{it.get('score', 0)} | "
                    f"{b_str} | "
                    f"[Apply]({it.get('apply_url') or it.get('url', '')}) |"
                )
            lines.append("")

        # 2. Hackathons
        if hackathons:
            target_hack = 8
            found_hack = len(hackathons)
            hack_subtitle = f"*(only {found_hack} found — target: {target_hack})*" if found_hack < target_hack else f"*({found_hack} verified)*"
            lines.append(f"## 🚀 Hackathons {hack_subtitle}")
            lines.append("| Hackathon | Organiser | Location | Deadline | Prize | Apply |")
            lines.append("|---|---|---|---|---|---|")
            for h in hackathons[:8]:
                slug = self._slugify(h.get("title", ""))
                nox_id = h.get("item_id", "")
                lines.append(
                    f"| [[{slug}--{nox_id}|{h.get('title', '')}]] | "
                    f"{h.get('organiser') or h.get('org') or 'Unknown'} | "
                    f"{h.get('city_or_country') or h.get('country', 'unknown')} | "
                    f"{h.get('deadline', 'unknown')} | "
                    f"{h.get('prize_text') or h.get('prize', 'unknown')} | "
                    f"[Apply]({h.get('apply_url') or h.get('url', '')}) |"
                )
            lines.append("")

        # 3. Competitions (CTF capped)
        if competitions:
            lines.append("## 🏆 Competitions (CTF Capped)")
            lines.append("| Competition | Topic | Deadline | Link |")
            lines.append("|---|---|---|---|")
            for op in competitions[:5]:
                slug = self._slugify(op.get("title", ""))
                nox_id = op.get("item_id", "")
                lines.append(
                    f"| [[{slug}--{nox_id}|{op.get('title', '')}]] | "
                    f"{op.get('topic_name', 'General')} | "
                    f"{op.get('deadline', 'unknown')} | "
                    f"[Details]({op.get('url') or op.get('apply_url', '')}) |"
                )
            lines.append("")

        # 4. News Topics sections
        if news_items:
            lines.append("## 📰 News by Topic")
            by_topic: dict[str, list[dict[str, Any]]] = {}
            for it in news_items:
                tname = it.get("topic_name") or "General"
                by_topic.setdefault(tname, []).append(it)

            for tname, t_items in by_topic.items():
                lines.append(f"### [[{tname}]]")
                for it in t_items[:4]:
                    slug = self._slugify(it.get("title", ""))
                    nox_id = it.get("item_id", "")
                    takeaway = it.get("takeaway") or it.get("summary", "")
                    lines.append(
                        f"- [[{slug}--{nox_id}|{it.get('title', '')}]] — {takeaway[:120]}... "
                        f"([source]({it.get('url', '')}))"
                    )
                lines.append("")

        # 5. Startup Ideas
        if ideas:
            lines.append("## 💡 Startup Ideas & Problems")
            for id_item in ideas[:3]:
                slug = self._slugify(id_item.get("title", ""))
                nox_id = id_item.get("item_id", "")
                lines.append(
                    f"- [[{slug}--{nox_id}|{id_item.get('title', '')}]]: "
                    f"{id_item.get('summary', '')} "
                    f"([source]({id_item.get('url', '')}))"
                )
            lines.append("")

        # 6. How this was made
        drop_reasons = analyst_data.get("drop_reasons", {})
        drop_summary = (
            ", ".join(f"{k}: {v}" for k, v in drop_reasons.items())
            if drop_reasons
            else "link_failed: 0, ineligible: 0, duplicate: 0"
        )
        stage_timings = analyst_data.get("stage_timings", {})
        timing_str = (
            ", ".join(f"{k}: {v:.2f}s" for k, v in stage_timings.items())
            if stage_timings
            else f"total: {generation_seconds:.2f}s"
        )
        engine_label = (
            "Local (Degraded — Cloud Disabled)"
            if analyst_data.get("is_degraded")
            else "NoX Intelligence Briefing Pipeline (Session K Verified)"
        )
        lines.extend(
            [
                "## 🛠️ How this was made",
                f"- **Internships found**: {len(internships)} (target: 10)",
                f"- **Hackathons found**: {len(hackathons)} (target: 8)",
                f"- **Competitions found**: {len(competitions)} (target: 5, CTF capped at 3)",
                f"- **Sources scanned**: {analyst_data.get('sources_count', len(items))}",
                f"- **Execution time**: {generation_seconds:.2f}s ({timing_str})",
                f"- **Drop reasons**: {drop_summary}",
                f"- **Engine**: {engine_label}",
            ]
        )

        return self.write_file_atomic(rel_path, "\n".join(lines), meta)

    def export_topic_hub(self, topic_name: str, recent_items: list[dict[str, Any]]) -> Path:
        """Export/update Topic hub note in Topics/<Topic>.md."""
        rel_path = f"Topics/{topic_name}.md"
        meta = {
            "topic": topic_name,
            "type": "hub",
            "updated": time.strftime("%Y-%m-%d"),
        }
        lines = [
            f"# {topic_name}",
            "",
            "## Recent Curated Items",
        ]
        for it in recent_items[:15]:
            slug = self._slugify(it.get("title", "Item"))
            nox_id = it.get("item_id", "")
            lines.append(
                f"- [[{slug}--{nox_id}|{it.get('title')}]] — "
                f"{it.get('published_at', '')} ({it.get('source_name', 'Source')})"
            )
        return self.write_file_atomic(rel_path, "\n".join(lines), meta)

    def regenerate_dashboards(self) -> None:
        """Regenerate Dashboards with Dataview code blocks and static Markdown tables."""
        items_dir = self.vault_path / "Items"
        if not items_dir.exists():
            return

        internships: list[dict[str, Any]] = []
        hackathons: list[dict[str, Any]] = []
        opps: list[dict[str, Any]] = []
        reading_queue: list[dict[str, Any]] = []

        # Read notes frontmatter
        for md_file in items_dir.glob("*/*.md"):
            try:
                post = frontmatter.loads(md_file.read_text(encoding="utf-8"))
                meta = post.metadata
                meta["_file"] = md_file.stem
                itype = meta.get("type")
                if itype == "internship":
                    internships.append(meta)
                elif itype == "hackathon":
                    hackathons.append(meta)
                elif itype == "opportunity":
                    opps.append(meta)
                if meta.get("status") in ("new", "saved"):
                    reading_queue.append(meta)
            except Exception as exc:
                log.debug("Failed reading metadata from %s: %s", md_file, exc)
                continue

        # 1. Internships Tracker
        int_lines = [
            "# Internships Tracker",
            "",
            "```dataview",
            (
                'TABLE org, country, deadline, score, benefits.accommodation AS housing, '
                'benefits.stipend AS stipend, status FROM "Items/Internships" '
                'WHERE status != "dismissed" SORT score DESC'
            ),
            "```",
            "",
            "## Active Internships (Static View)",
            (
                "| Program | Organisation | Location | Deadline | "
                "Score | Housing | Stipend | Status | Apply |"
            ),
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for it in sorted(internships, key=lambda x: int(x.get("score", 0)), reverse=True):
            b = it.get("benefits", {})
            h_str = b.get("accommodation", "unknown") if isinstance(b, dict) else "unknown"
            s_str = b.get("stipend", "unknown") if isinstance(b, dict) else "unknown"
            int_lines.append(
                f"| [[{it['_file']}|{it.get('title', it['_file'])}]] | "
                f"{it.get('org', 'Unknown')} | "
                f"{it.get('country', 'unknown')} | "
                f"{it.get('deadline', 'TBD')} | "
                f"{it.get('score', 0)} | "
                f"{h_str} | "
                f"{s_str} | "
                f"{it.get('status', 'new')} | "
                f"[Apply]({it.get('apply_url') or it.get('url', '')}) |"
            )
        self.write_file_atomic("Dashboards/Internships Tracker.md", "\n".join(int_lines))

        # 2. Hackathons Tracker
        hack_lines = [
            "# Hackathons Tracker",
            "",
            "```dataview",
            (
                'TABLE org, deadline, prize, mode, score, status FROM "Items/Hackathons" '
                'WHERE status != "dismissed" SORT deadline ASC'
            ),
            "```",
            "",
            "## Active Hackathons (Static View)",
            (
                "| Hackathon | Organiser | Location / Mode | "
                "Deadline | Prize | Score | Status | Apply |"
            ),
            "|---|---|---|---|---|---|---|---|",
        ]
        for hk in sorted(hackathons, key=lambda x: str(x.get("deadline", "9999"))):
            hack_lines.append(
                f"| [[{hk['_file']}|{hk.get('title', hk['_file'])}]] | "
                f"{hk.get('org', 'Unknown')} | "
                f"{hk.get('country', hk.get('mode', 'unknown'))} | "
                f"{hk.get('deadline', 'TBD')} | "
                f"{hk.get('prize', 'TBD')} | "
                f"{hk.get('score', 0)} | "
                f"{hk.get('status', 'new')} | "
                f"[Apply]({hk.get('apply_url') or hk.get('url', '')}) |"
            )
        self.write_file_atomic("Dashboards/Hackathons.md", "\n".join(hack_lines))

        # 3. Opportunities Board
        opp_lines = [
            "# Opportunities Board",
            "",
            "```dataview",
            (
                'TABLE deadline, prize, mode FROM "Items/Opportunities" '
                'WHERE status != "dismissed" SORT deadline ASC'
            ),
            "```",
            "",
            "## Active Opportunities (Static View)",
            "| Opportunity | Topic | Deadline | Prize | Status |",
            "|---|---|---|---|---|",
        ]
        for op in opps:
            opp_lines.append(
                f"| [[{op['_file']}|{op.get('title', op['_file'])}]] | "
                f"{op.get('topic', 'General')} | "
                f"{op.get('deadline', 'TBD')} | "
                f"{op.get('prize', 'TBD')} | "
                f"{op.get('status', 'new')} |"
            )
        self.write_file_atomic("Dashboards/Opportunities Board.md", "\n".join(opp_lines))

        # 4. Deadlines Dashboard
        deadlines_lines = [
            "# Upcoming Deadlines",
            "",
            "```dataview",
            (
                'TABLE deadline, prize FROM "Items/Opportunities" '
                'WHERE deadline != "TBD" SORT deadline ASC'
            ),
            "```",
            "",
            "## Chronological Deadlines (Static View)",
            "| Item | Deadline | Prize |",
            "|---|---|---|",
        ]
        for op in sorted(opps, key=lambda x: str(x.get("deadline", "9999"))):
            deadlines_lines.append(
                f"| [[{op['_file']}|{op.get('title', op['_file'])}]] | "
                f"{op.get('deadline', 'TBD')} | "
                f"{op.get('prize', 'TBD')} |"
            )
        self.write_file_atomic("Dashboards/Deadlines.md", "\n".join(deadlines_lines))

        # 5. Reading Queue
        queue_lines = [
            "# Reading Queue",
            "",
            "```dataview",
            (
                'TABLE topic, source, published FROM "Items" '
                'WHERE status = "new" OR status = "saved" SORT published DESC'
            ),
            "```",
            "",
            "## Unread Items (Static View)",
            "| Note | Topic | Source | Published |",
            "|---|---|---|---|",
        ]
        for it in reading_queue[:30]:
            queue_lines.append(
                f"| [[{it['_file']}|{it.get('title', it['_file'])}]] | "
                f"{it.get('topic', 'General')} | "
                f"{it.get('source', 'Unknown')} | "
                f"{it.get('published', '')} |"
            )
        self.write_file_atomic("Dashboards/Reading Queue.md", "\n".join(queue_lines))

    def export_conversation_thread(
        self,
        conv_id: str,
        title: str,
        summary: str,
        decisions: list[str],
        open_questions: list[str],
        links: list[str],
        date_str: str | None = None,
    ) -> Path:
        """Export local thread digest note to Threads/YYYY-MM-DD <title>--<conv_id>.md."""
        d_str = date_str or time.strftime("%Y-%m-%d")
        slug = self._slugify(title, max_len=40)
        rel_path = f"Threads/{d_str} {slug}--{conv_id}.md"

        meta = {
            "conv_id": conv_id,
            "title": title,
            "date": d_str,
            "tags": ["thread", "nox-chat"],
        }

        lines = [
            f"# {title}",
            "",
            f"**Conversation**: `{conv_id}` | **Date**: {d_str}",
            "",
            "## Summary",
            summary,
            "",
            "## Decisions Made",
        ]
        for dec in decisions:
            lines.append(f"- {dec}")
        if not decisions:
            lines.append("- *(No formal decisions recorded)*")

        lines.extend(["", "## Open Questions"])
        for q in open_questions:
            lines.append(f"- {q}")
        if not open_questions:
            lines.append("- *(None)*")

        lines.extend(["", "## Key Links & References"])
        for lk in links:
            lines.append(f"- {lk}")
        if not links:
            lines.append("- *(None)*")

        return self.write_file_atomic(rel_path, "\n".join(lines), meta)

    def export_briefing_run(
        self,
        run_id: str,
        date_str: str,
        slot: str,
        analyst_data: dict[str, Any],
        generation_seconds: float,
    ) -> Path:
        """Export all items, topic hubs, dashboards, and the digest note."""
        items = analyst_data.get("items", [])
        title = f"NoX Briefing — {date_str} {slot.capitalize()}"

        # 1. Export each item note
        for it in items:
            try:
                self.export_item_note(it, date_str, slot)
            except Exception as exc:
                log.debug("Error exporting item note: %s", exc)

        # 2. Update Topic hub notes
        by_topic: dict[str, list[dict[str, Any]]] = {}
        for it in items:
            tname = it.get("topic_name") or "General"
            by_topic.setdefault(tname, []).append(it)

        for tname, t_items in by_topic.items():
            try:
                self.export_topic_hub(tname, t_items)
            except Exception as exc:
                log.debug("Error updating topic hub %s: %s", tname, exc)

        # 3. Regenerate dashboards
        try:
            self.regenerate_dashboards()
        except Exception as exc:
            log.debug("Error regenerating dashboards: %s", exc)

        # 4. Export digest note
        digest_path = self.export_digest_note(
            date_str, slot, title, analyst_data, generation_seconds
        )
        log.info("Exported briefing digest note to vault: %s", digest_path)
        return digest_path

    async def sync_vault_to_db(self, db: aiosqlite.Connection | None = None) -> dict[str, int]:
        """Read back user-edited frontmatter (status, rating) and sync to DB."""
        items_dir = self.vault_path / "Items"
        if not items_dir.exists():
            return {"scanned": 0, "synced": 0}

        scanned = 0
        synced = 0

        async def _do_sync(conn: aiosqlite.Connection) -> None:
            nonlocal scanned, synced
            for md_file in items_dir.glob("*/*.md"):
                scanned += 1
                try:
                    post = frontmatter.loads(md_file.read_text(encoding="utf-8"))
                    meta = post.metadata
                    nox_id = meta.get("nox_id")
                    if not nox_id:
                        continue

                    rating = meta.get("rating")
                    if rating in ("like", "dislike"):
                        # Check if already present in briefing_feedback
                        existing_fb = await fetch_one(
                            conn,
                            "SELECT id FROM briefing_feedback WHERE item_id = ? AND rating = ?",
                            (nox_id, rating),
                        )
                        if not existing_fb:
                            fb_id = f"fb_vault_{uuid.uuid4().hex[:8]}"
                            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                            await execute(
                                conn,
                                """
                                INSERT INTO briefing_feedback
                                (id, briefing_id, topic_id, item_id,
                                 rating, reason_text, created_at)
                                VALUES (?, ?, ?, ?, ?, 'Synced from Notes Vault user rating', ?)
                                """,
                                (
                                    fb_id,
                                    meta.get("briefing", "vault"),
                                    meta.get("topic", "general"),
                                    nox_id,
                                    rating,
                                    now_iso,
                                ),
                            )
                            synced += 1

                    user_status = meta.get("status")
                    target_url = meta.get("url") or meta.get("apply_url")
                    if target_url and user_status in (
                        "new",
                        "interested",
                        "applied",
                        "interview",
                        "offer",
                        "rejected",
                        "dismissed",
                    ):
                        await execute(
                            conn,
                            """
                            UPDATE opportunity_registry
                            SET status = ?, last_changed = (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                            WHERE canonical_url = ?
                            """,
                            (user_status, target_url),
                        )
                except Exception as exc:
                    log.debug("Error reading note frontmatter from %s: %s", md_file, exc)
            await conn.commit()

        if db is not None:
            await _do_sync(db)
        else:
            async with get_db() as conn:
                await _do_sync(conn)

        return {"scanned": scanned, "synced": synced}

    def get_vault_uri(self) -> str:
        """Return obsidian://open URI for this vault."""
        return f"obsidian://open?vault={quote(self.vault_path.name)}"
