#!/usr/bin/env python3
"""scripts/probe_chat.py — End-to-end chat probe measuring latency, tokens, models, and trace.

Authenticates exactly as the UI does:
1. Reads ~/.local/share/nox/session_token
2. Exchanges token for nox_session cookie via GET /?t=<token>
3. Subscribes to /ws
4. Sends POST /api/chat
5. Measures time to first token, time to message_done, model_used, trace, notice, text length.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import websockets

TOKEN_PATH = Path(os.path.expanduser("~/.local/share/nox/session_token"))
BASE_HTTP = "http://127.0.0.1:7777"
BASE_WS = "ws://127.0.0.1:7777/ws"


async def probe_single_turn(
    prompt: str,
    allow_cloud: bool = True,
    timeout: float = 60.0,
    verbose: bool = True,
) -> dict[str, Any]:
    """Execute a single chat turn probe end-to-end."""
    if not TOKEN_PATH.exists():
        raise RuntimeError(f"Session token not found at {TOKEN_PATH}. Is noxd running?")

    token = TOKEN_PATH.read_text(encoding="utf-8").strip()

    # 1. Exchange token for cookie
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{BASE_HTTP}/",
            params={"t": token},
            follow_redirects=False,
        )
        cookies = dict(resp.cookies)
        session_cookie = cookies.get("nox_session") or token

    headers = {
        "X-Nox-Client": "web",
        "Authorization": f"Bearer {token}",
    }
    client_cookies = {"nox_session": session_cookie}

    # 2. Connect to WebSocket
    ws_url = f"{BASE_WS}?t={token}"
    ws_headers = [("Cookie", f"nox_session={session_cookie}")]

    result: dict[str, Any] = {
        "prompt": prompt,
        "allow_cloud": allow_cloud,
        "first_token_sec": None,
        "message_done_sec": None,
        "total_sec": None,
        "models_used": [],
        "notices": [],
        "trace": None,
        "final_text": "",
        "final_text_len": 0,
        "tokens_received": 0,
        "error": None,
    }

    t0 = time.perf_counter()

    try:
        async with websockets.connect(
            ws_url,
            additional_headers=ws_headers,
            open_timeout=10.0,
        ) as ws:
            # 3. Send POST /api/chat
            async with httpx.AsyncClient(
                cookies=client_cookies, headers=headers, timeout=10.0
            ) as http_client:
                chat_resp = await http_client.post(
                    f"{BASE_HTTP}/api/chat",
                    json={"message": prompt, "allow_cloud": allow_cloud},
                )
                if chat_resp.status_code != 200:
                    result["error"] = (
                        f"POST /api/chat returned HTTP {chat_resp.status_code}: {chat_resp.text}"
                    )
                    return result

                run_info = chat_resp.json()
                run_id = run_info.get("run_id")
                result["run_id"] = run_id

            # 4. Stream events from WebSocket
            token_chunks: list[str] = []
            while True:
                elapsed = time.perf_counter() - t0
                if elapsed > timeout:
                    result["error"] = f"Timeout ({timeout}s) waiting for run completion"
                    break

                try:
                    raw_msg = await asyncio.wait_for(ws.recv(), timeout=timeout - elapsed)
                except TimeoutError:
                    result["error"] = f"Timeout ({timeout}s) waiting for websocket message"
                    break

                try:
                    event = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                ev_type = event.get("type")
                ev_data = event.get("data") or {}
                ev_run_id = event.get("run_id")

                if verbose and ev_type != "token":
                    print(f"    [WS event] type={ev_type} run_id={ev_run_id}")

                # Filter events for this run if run_id is specified
                if ev_run_id and run_id and ev_run_id != run_id:
                    continue

                if ev_type == "token":
                    if result["first_token_sec"] is None:
                        result["first_token_sec"] = round(time.perf_counter() - t0, 3)
                    token_chunks.append(
                        ev_data.get("chunk") or ev_data.get("text") or ev_data.get("delta") or ""
                    )
                    result["tokens_received"] += 1

                elif ev_type == "model_used":
                    result["models_used"].append(ev_data)

                elif ev_type == "notice":
                    msg = ev_data.get("message") or str(ev_data)
                    result["notices"].append(msg)

                elif ev_type == "trace":
                    result["trace"] = ev_data

                elif ev_type == "message_done":
                    if result["message_done_sec"] is None:
                        result["message_done_sec"] = round(time.perf_counter() - t0, 3)
                    if not token_chunks and ev_data.get("content"):
                        token_chunks.append(ev_data["content"])

                elif ev_type in ("run_done", "run_failed", "run_cancelled"):
                    result["total_sec"] = round(time.perf_counter() - t0, 3)
                    if ev_type == "run_failed":
                        result["error"] = ev_data.get("error", "Run failed")
                    break

            result["final_text"] = "".join(token_chunks)
            result["final_text_len"] = len(result["final_text"])

    except Exception as exc:
        result["error"] = str(exc)

    if result["total_sec"] is None:
        result["total_sec"] = round(time.perf_counter() - t0, 3)

    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description="Probe NoX chat latency and path end-to-end")
    parser.add_argument("prompts", nargs="*", help="Prompts to test")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-turn timeout in seconds")
    parser.add_argument(
        "--no-cloud", action="store_true", help="Set allow_cloud=False in chat request"
    )
    args = parser.parse_args()

    prompts = args.prompts or [
        "Hello",
        "What is the capital of France?",
        "Explain what a mutex is in two sentences.",
    ]

    results = []
    for p in prompts:
        if not args.json:
            print("\n=======================================================")
            print(f"PROBING: {p!r}")
            print("=======================================================")
        res = await probe_single_turn(
            p,
            allow_cloud=not args.no_cloud,
            timeout=args.timeout,
        )
        results.append(res)

        if not args.json:
            print(f"  Run ID:             {res.get('run_id')}")
            print(f"  First token (TTFT): {res.get('first_token_sec')} s")
            print(f"  Message done:       {res.get('message_done_sec')} s")
            print(f"  Total turn time:    {res.get('total_sec')} s")
            print(f"  Models used:        {res.get('models_used')}")
            print(f"  Notices:            {res.get('notices')}")
            print(f"  Tokens received:    {res.get('tokens_received')}")
            print(f"  Output length:      {res.get('final_text_len')} chars")
            print(f"  Preview:            {res.get('final_text')[:120]!r}")
            if res.get("trace"):
                tr = res["trace"]
                print(f"  Trace intent:       {tr.get('intent')}")
                print(f"  Trace stages:       {tr.get('stages')}")
                print(f"  Trace total:        {tr.get('total_ms')} ms")
                print(f"  Trace fallback:     {tr.get('fallback_reason')}")
            if res.get("error"):
                print(f"  ERROR:              {res.get('error')}")

    if args.json:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
