"""WebSocket endpoint for real-time streaming events and ping/pong replay per Section 6.1."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from noxai.api.auth import verify_websocket_auth
from noxai.events import Event, EventBus, global_event_bus

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

ws_router = APIRouter()


def get_event_bus() -> EventBus:
    return global_event_bus


@ws_router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    run_id: str | None = None,
) -> None:
    """Real-time event streaming WebSocket.

    Pushes system events, streamed tokens, tool calls, and plan progress.
    Supports event replay (last 200 events) and client ping/pong.
    """
    bus = get_event_bus()

    # 1. Origin and authentication validation
    if not verify_websocket_auth(websocket):
        log.warning("WebSocket rejected unauthorized connection attempt.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    log.info("WebSocket client connected.")

    # 2. Replay recent/active run events (last 200)
    if run_id:
        replay_list = bus.get_replay_events(run_id)
    else:
        replay_list = bus.get_recent_events(limit=200)

    for ev in replay_list:
        try:
            await websocket.send_json(ev.to_dict())
        except Exception as exc:
            log.debug("Failed sending replay event: %s", exc)
            break

    # 3. Subscribe live connection
    async def _on_event(event: Event) -> None:
        try:
            await websocket.send_json(event.to_dict())
        except Exception as exc:
            log.debug("WebSocket client closed during event delivery: %s", exc)

    unsubscribe = bus.subscribe(_on_event)

    # 4. Message loop
    try:
        while True:
            msg = await websocket.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        log.info("WebSocket client disconnected.")
    except Exception as exc:
        log.debug("WebSocket exception: %s", exc)
    finally:
        unsubscribe()
