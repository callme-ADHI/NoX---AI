"""Channels subsystem for NoX.

Provides interactive chat channel integrations:
- Telegram (owner pairing, commands, privacy gate)
- Slack (team ingestion, guarded posting)
- Discord (server ingestion, guarded posting)
"""

from noxai.channels.base import BaseChannel, ChannelRegistry
from noxai.channels.discord import DiscordChannel
from noxai.channels.slack import SlackChannel
from noxai.channels.telegram import TelegramChannel

__all__ = [
    "BaseChannel",
    "ChannelRegistry",
    "DiscordChannel",
    "SlackChannel",
    "TelegramChannel",
]
