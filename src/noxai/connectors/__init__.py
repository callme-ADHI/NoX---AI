"""Connectors subsystem for NoX.

Provides unified connectors for external services:
- Gmail (OAuth PKCE)
- IMAP (SSL)
- Calendar (Google Calendar + ICS)
- Feeds, Telegram, Slack, Discord, GitHub
"""

from noxai.connectors.base import BaseConnector, ConnectorRegistry
from noxai.connectors.calendar import CalendarConnector
from noxai.connectors.feeds import FeedsConnector
from noxai.connectors.github import GitHubConnector
from noxai.connectors.gmail import GmailConnector
from noxai.connectors.imap import IMAPConnector
from noxai.connectors.mail_profile import (
    DigestGenerator,
    MailClassifier,
    MailProfile,
    PhishingDetector,
)

__all__ = [
    "BaseConnector",
    "CalendarConnector",
    "ConnectorRegistry",
    "DigestGenerator",
    "FeedsConnector",
    "GitHubConnector",
    "GmailConnector",
    "IMAPConnector",
    "MailClassifier",
    "MailProfile",
    "PhishingDetector",
]
