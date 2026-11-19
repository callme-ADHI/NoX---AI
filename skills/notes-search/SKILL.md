---
name: notes-search
description: Instructions for recalling knowledge from the user's Obsidian vault and Study notes, including citation format and file path references
version: 1.0.0
author: NoX Core Team
license: Apache-2.0
---

# User Notes and Document Knowledge Recall Guidelines

When responding to queries about the user's personal notes, Obsidian vault, academic courseware, or study documents, adhere to the following recall guidelines:

## 1. Grounding in Local Documents
- Ground your answers in the excerpts retrieved from the user's local notes (`~/Documents/Obsidian Vault`, `~/Documents/Study`).
- Prefer exact statements and technical notes written by the user over generic general knowledge.

## 2. Citation Requirements
- ALWAYS cite the exact absolute or relative file path of the note containing the information:
  Example: `According to your notes in **`/home/boss/Documents/Obsidian Vault/MOM.md`** (Lines 1-15):`
- When applicable, mention the specific markdown heading or section:
  Example: `Under heading 'Database Architecture > Sharding':`

## 3. Direct Quotations
- Quote the most relevant lines verbatim using blockquotes (`>`) to preserve the author's original phrasing, abbreviations, and informal shorthand.
- Clearly differentiate between what the user wrote and any complementary technical synthesis.

## 4. Unfound Topics
- If the requested topic or keyword does not appear in the retrieved notes, state clearly that the topic was not found in the indexed notes rather than hallucinating note contents.
