---
name: briefing-format
description: Formatting rules and section structures for NoX executive briefings with category organization and clickable markdown citations
version: 1.0.0
author: NoX Core Team
license: Apache-2.0
---

# NoX Intelligence Briefing Formatting Guidelines

When generating or presenting a morning or evening briefing, format the intelligence digest according to the following strict conventions:

## 1. Executive Summary
- Begin with an executive 3-line TL;DR summarizing top strategic developments across all domains.
- Mention active date and session cycle (e.g. `Morning Briefing — Asia/Kolkata`).

## 2. Categorical Sections
Organize items into thematic domain blocks:
- **Cybersecurity & Threat Intelligence**: Critical CVEs, exploited vulnerabilities, active malware campaigns, offensive tool releases.
- **AI & Frontier Systems**: Foundation model releases, reasoning architectures, multimodal breakthroughs, open weights releases.
- **Regional Ecosystem & Programs**: Competitions, CTFs, hackathons, and internship deadlines matching the user's career profile.

## 3. Item Entry Structure
Each item within a category must follow this compact schema:
- **Headline**: High-impact, factual, and direct summary title.
- **What Happened**: 1-2 concise sentences summarizing the core development.
- **Strategic Impact**: 1 sentence analyzing practical consequences for system security or engineering.
- **Primary Source**: A clickable Markdown link directly citing the canonical source URL (e.g. `[NVD Advisory](https://nvd.nist.gov/...)` or `[GitHub Release](https://github.com/...)`).

## 4. Integrity and Zero-Hallucination
- If no verified updates were detected for a category in the current cycle, output a single line: `No significant updates in this category for this cycle.`
- Never fabricate sources, CVE identifiers, or benchmark scores.
