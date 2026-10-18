"""Delivery channels for job outputs and reminders per Section 6.7."""

from __future__ import annotations

import asyncio
import logging
import shutil
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from noxai.events import EventBus, global_event_bus, make_event

if TYPE_CHECKING:
    import aiosqlite

log = logging.getLogger(__name__)


class DeliveryChannel(ABC):
    """Abstract interface for delivery channels (e.g. UI, Desktop notification)."""

    @abstractmethod
    async def deliver(
        self,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> bool:
        """Deliver a notification or briefing message."""
        ...


class UIDeliveryChannel(DeliveryChannel):
    """Deliver messages to the UI via the EventBus."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self.event_bus = event_bus or global_event_bus

    async def deliver(
        self,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> bool:
        event_data = {
            "title": title,
            "message": message,
            "payload": payload or {},
        }
        event_type = (
            "reminder_fired" if payload and payload.get("kind") == "reminder" else "notification"
        )
        try:
            await self.event_bus.publish(make_event(event_type, event_data), db=db)
            return True
        except Exception as exc:
            log.warning("UIDeliveryChannel failed to publish event: %s", exc)
            return False


class DesktopDeliveryChannel(DeliveryChannel):
    """Deliver desktop notifications via notify-send on Linux/Hyprland."""

    def __init__(self, app_name: str = "NoX") -> None:
        self.app_name = app_name

    async def deliver(
        self,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> bool:
        binary = shutil.which("notify-send")
        if not binary:
            log.info("DesktopDeliveryChannel: notify-send not found on system; skipped.")
            return False

        # Keep short summary for desktop banner
        summary = title[:120]
        body = message[:300]
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                f"--app-name={self.app_name}",
                summary,
                body,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception as exc:
            log.warning("DesktopDeliveryChannel notify-send execution failed: %s", exc)
            return False


class DeliveryRouter:
    """Dispatches notifications across requested channels (e.g. 'ui', 'desktop')."""

    def __init__(
        self,
        channels: dict[str, DeliveryChannel] | None = None,
    ) -> None:
        self.channels: dict[str, DeliveryChannel] = channels or {
            "ui": UIDeliveryChannel(),
            "desktop": DesktopDeliveryChannel(),
        }

    async def deliver(
        self,
        channel_names: list[str],
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for name in channel_names:
            ch = self.channels.get(name)
            if ch is not None:
                ok = await ch.deliver(title, message, payload=payload, db=db)
                results[name] = ok
            else:
                log.warning("Unknown delivery channel requested: '%s'", name)
                results[name] = False
        return results
