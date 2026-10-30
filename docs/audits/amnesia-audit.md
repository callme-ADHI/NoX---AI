# Amnesia Bug Codebase Audit

**Date**: 2026-10-30  
**Status**: Confirmed across all 7 routes via `tests/test_conversation_memory_repro.py`

---

## 1. Executive Summary

When a user engages in multi-turn conversation (e.g. Turn 1: "Explain RAG briefly.", Turn 2: "Why is that useful?"), NoX exhibits total context amnesia. In Turn 2, the underlying model receives strictly:
`[System Prompt, Message(role='user', content='Why is that useful?')]`

The model never receives Turn 1's user prompt or assistant response, causing follow-ups to fail or be treated as disconnected questions.

---

## 2. File:Line Audit & Tracing

### 2.1 Where messages are persisted
- **User messages**:
  - `src/noxai/orchestrator.py:306-313`: Inserts `user` role message into `messages` table (`INSERT INTO messages (id, conversation_id, role, content, taint_flags)`).
  - `src/noxai/agent/loop.py:350-362`: If `db` is passed, inserts `user` role message into `messages` table.
- **Assistant replies**:
  - `src/noxai/orchestrator.py:734-741`: Inserts `assistant` role message into `messages` table upon turn completion.
  - `src/noxai/agent/loop.py:804-816`: Inserts `assistant` role message upon completion.
- **Tool calls and tool results**:
  - `src/noxai/orchestrator.py:570-580` & `635-645`: Tool calls and results are kept in memory during the active turn in `messages_to_send`, but tool call messages and tool result messages are NOT inserted into the SQLite `messages` table! Only final text is saved as `role='assistant'`.

### 2.2 How `conversation_id` is created and passed
- **UI (`ui/src/pages/ChatPage.tsx`)**:
  - `ChatPage.tsx:103-108`: On load, checks `sessionStorage.getItem('nox_active_conv')`. If none exists, selects the first conversation in `api.getConversations()` or leaves `activeConvId` empty.
  - `ChatPage.tsx:320, 377, 394`: Sends `conversation_id: activeConvId || ''` in payload to `/api/chat`.
  - `ChatPage.tsx:413`: On receiving response from `/api/chat`, stores `resp.conversation_id` into `sessionStorage`.
- **WebSocket Reconnect**:
  - `ui/src/services/websocket.ts:35-50`: WebSocket is purely an event delivery pipe. On disconnect/reconnect, it reconnects to `/ws?token=...`. It does not reset `activeConvId`, but if the user reloads the browser tab and `sessionStorage` was cleared or not yet populated, `activeConvId` is null, generating a brand new conversation ID!
- **REST Endpoint (`src/noxai/api/routes.py:453`)**:
  - `routes.py:453`: `conv_id = req.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"`. If frontend sends empty string `""`, it treats it as falsy and generates a new conversation ID!
- **CLI (`src/noxai/cli.py:295-331`)**:
  - `cli.py:295`: `chat_cmd` accepts `message: str`, but has NO option `--conv` or `--continue`!
  - `cli.py:323`: Calls `orchestrate_chat_turn(message=message, ...)` without passing `conversation_id`.
  - `src/noxai/orchestrator.py:233`: `actual_conv_id = conversation_id or f"conv_{uuid.uuid4().hex[:12]}"`. Every CLI invocation generates a brand new conversation ID, guaranteeing 100% amnesia between commands.

### 2.3 Where history is loaded and how it is truncated
- **Orchestrator (`src/noxai/orchestrator.py:368-416`)**:
  - **CRITICAL FLAW**: In `orchestrate_chat_turn`, line 373:
    `cloud_turn_messages = [sys_msg, user_msg]`
  - Line 403:
    `messages_to_send = [_build_local_system_prompt(), user_msg]`
  - Line 414:
    `messages_to_send = [sys_msg, user_msg]`
  - **Zero history loading exists in `orchestrator.py`!** SQLite `messages` table is never queried for past turns of `conversation_id`.
- **Agent Loop (`src/noxai/agent/loop.py:307-330`)**:
  - Line 307: `if not allow_cloud and conversation_id and db is not None:`
  - **CRITICAL FLAW**: If `allow_cloud=True` (the default for all front-desk operations), history loading is explicitly bypassed!
  - If `allow_cloud=False`, it loaded all previous messages with `SELECT role, content ... ORDER BY created_at ASC`, but without token budgeting, summarization, or entity tracking.

### 2.4 Whether the reflex classifier or fast path bypasses history
- `src/noxai/orchestrator.py:251`:
  `intent_res = analyze_intent(message)`
- The reflex router inspects *only* the current turn `message`. It does not inspect `conv_state`, previous user intent, or previous assistant answers.
- A short follow-up like "Why is that useful?" is analyzed in total isolation without the context of "Explain RAG briefly."

### 2.5 Whether the cloud front-desk request includes history
- `src/noxai/orchestrator.py:373`: `cloud_turn_messages = [sys_msg, user_msg]`
- **No.** Cloud front desk turns are strictly passed `[sys_msg, user_msg]`.

### 2.6 Ambiguous/follow-up message classification
- `src/noxai/agent/intent.py`:
  Messages like "Why?", "More", "And that one?" have no context of `last_intent` or `last_route`, leading to misclassification or being evaluated without antecedent reference.

### 2.7 Whether assistant's own previous replies and tool results are stored and replayed
- While the assistant's final text is written to `messages` at `orchestrator.py:734-741`, it was never retrieved into prompt context for subsequent turns. Intermediate tool calls and tool results were discarded at the end of the turn.

### 2.8 Whether interrupted or streamed replies are saved
- `src/noxai/orchestrator.py:660-691`: If an exception or cancel occurs, or a stream aborts, no partial message is written with `partial=1`. The aborted turn leaves no trace in `messages`.

### 2.9 Whether UI "New Chat" is triggered accidentally on reconnect
- In `ChatPage.tsx:102-107`, if `getConversations()` returns an empty list or fails, or if `sessionStorage` has not persisted `nox_active_conv`, `activeConvId` defaults to empty, triggering a new conversation on the next prompt.

---

## 3. Minimal Fix Strategy for Phase 1

To make `tests/test_conversation_memory_repro.py` pass across all routes:
1. In `src/noxai/orchestrator.py`, query `messages` for `conversation_id` (both user and assistant turns) ordered by `created_at ASC` excluding the current turn.
2. In `cloud_turn_messages` and local `messages_to_send`, construct:
   `[sys_msg, *history_messages, user_msg]`
3. Ensure the egress filter inspects history messages whose taint allows it.
4. In `src/noxai/agent/loop.py`, load conversation history regardless of `allow_cloud` status.
5. In `src/noxai/cli.py`, add support for continuing the active/latest conversation.
