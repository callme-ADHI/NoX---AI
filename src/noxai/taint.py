"""Taint flags and the Content wrapper for NoX.

Every piece of text that flows through the agent pipeline is wrapped in a
``Content`` object that carries ``TaintFlag`` bits. The router and safety
layer check these flags to enforce privacy routing and taint escalation.

Design notes (§6.2, §6.5):
- ``PRIVATE``: data that must never leave the local machine (user chat,
  memory facts, file contents, conversation history).
- ``UNTRUSTED``: data from an external, uncontrolled source (web pages,
  RSS feeds, fetched files). Must be wrapped in ``<untrusted_content>``
  tags before being sent to the model, and escalates approval requirements
  for write/dangerous tool calls within the same run.

Flags are combined with bitwise OR when merging Content objects. The
resulting flags represent the *union* of taint across all inputs.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import ClassVar


class TaintFlag(enum.Flag):
    """Taint flags for content provenance and privacy routing.

    Flags can be combined: ``TaintFlag.PRIVATE | TaintFlag.UNTRUSTED``.
    """

    NONE = 0
    PRIVATE = enum.auto()  # must stay on-device; never sent to cloud
    UNTRUSTED = enum.auto()  # from web/RSS/file; must be wrapped in tags


class DataClass(enum.StrEnum):
    """Data classification for Milestone 10 privacy policy v2 (§1.2).

    Classes replace the earlier binary private flag with granular classification:
    - SECRET: API keys, tokens, passwords, private keys. Never sent to cloud.
    - LOCAL_ONLY: File contents, memory facts (unless cloud_ok), system files.
    - CHAT: User messages and conversation history. Allowed if chat_to_cloud=True.
    - PUBLIC: Web pages, search results, RSS items. Safe for cloud.
    """

    SECRET = "secret"  # noqa: S105
    LOCAL_ONLY = "local_only"
    CHAT = "chat"
    PUBLIC = "public"

    @property
    def rank(self) -> int:
        """Severity ranking for merging: higher rank dominates."""
        ranks = {
            DataClass.PUBLIC: 1,
            DataClass.CHAT: 2,
            DataClass.LOCAL_ONLY: 3,
            DataClass.SECRET: 4,
        }
        return ranks[self]

    def is_cloud_allowed(
        self,
        *,
        chat_to_cloud: bool = True,
        private_mode: bool = False,
    ) -> bool:
        """Return True if this data class is allowed to egress to cloud models."""
        if private_mode:
            return False
        if self == DataClass.PUBLIC:
            return True
        if self == DataClass.CHAT:
            return chat_to_cloud
        return False


@dataclass
class Content:
    """A piece of text with associated taint flags and an optional source label.

    Args:
        text: The raw text payload.
        taint: Combination of ``TaintFlag`` values for this content.
        source: Optional human-readable label (URL, file path, etc.)
                for attribution and debugging. Never used in routing decisions.
        data_class: Optional ``DataClass`` classification for privacy v2.
    """

    text: str
    taint: TaintFlag = TaintFlag.NONE
    source: str | None = None
    data_class: DataClass | None = None

    # Tag name used to wrap untrusted content before it enters the model
    UNTRUSTED_TAG: ClassVar[str] = "untrusted_content"

    def get_data_class(self) -> DataClass:
        """Return the effective DataClass for this content.

        If explicitly SECRET, return SECRET.
        If taint carries PRIVATE flag, the effective class is at least LOCAL_ONLY
        (PRIVATE taint cannot be downgraded to CHAT or PUBLIC).
        Otherwise return the explicit data_class or PUBLIC.
        """
        if self.data_class == DataClass.SECRET:
            return DataClass.SECRET
        if bool(self.taint & TaintFlag.PRIVATE):
            return DataClass.LOCAL_ONLY
        if self.data_class is not None:
            return self.data_class
        return DataClass.PUBLIC

    def is_private(self) -> bool:
        """Return True if content carries PRIVATE taint or is SECRET/LOCAL_ONLY."""
        if bool(self.taint & TaintFlag.PRIVATE):
            return True
        if self.data_class in (DataClass.SECRET, DataClass.LOCAL_ONLY):
            return True
        return False

    def is_untrusted(self) -> bool:
        """Return True if this content carries the UNTRUSTED flag."""
        return bool(self.taint & TaintFlag.UNTRUSTED)

    def is_clean(self) -> bool:
        """Return True if this content has no taint flags and is not SECRET or LOCAL_ONLY."""
        return self.taint == TaintFlag.NONE and self.data_class not in (
            DataClass.SECRET,
            DataClass.LOCAL_ONLY,
        )

    def as_model_text(self) -> str:
        """Return the text as it should be injected into the model context.

        Untrusted content is wrapped in XML-like tags. Any literal closing
        tag in the text is neutralised to prevent tag injection.
        Private content is returned as-is (the privacy check happens at the
        router level, before this method is called).
        """
        if not self.is_untrusted():
            return self.text

        # Strip any literal closing tags from the fetched content to prevent
        # an adversary from closing the wrapper and injecting instructions.
        safe_text = self.text.replace(
            f"</{self.UNTRUSTED_TAG}>",
            f"</{self.UNTRUSTED_TAG} />",  # neutralise
        )
        source_attr = f' source="{self.source}"' if self.source else ""
        return f"<{self.UNTRUSTED_TAG}{source_attr}>\n{safe_text}\n</{self.UNTRUSTED_TAG}>"

    def merge(self, other: Content) -> Content:
        """Return a new Content whose taint is the union of self and other.

        The text of the returned object is the concatenation of both texts
        with a newline separator. The resulting data class is the most
        restrictive (highest rank) between self and other.
        """
        self_dc = self.get_data_class()
        other_dc = other.get_data_class()
        merged_dc = self_dc if self_dc.rank >= other_dc.rank else other_dc
        return Content(
            text=self.text + "\n" + other.text,
            taint=self.taint | other.taint,
            source=self.source,  # keep the first source as the primary label
            data_class=merged_dc,
        )

    @classmethod
    def secret(cls, text: str, source: str | None = None) -> Content:
        """Convenience constructor for secret content (credentials, tokens)."""
        return cls(text=text, taint=TaintFlag.PRIVATE, source=source, data_class=DataClass.SECRET)

    @classmethod
    def local_only(cls, text: str, source: str | None = None) -> Content:
        """Convenience constructor for local-only content (system info, files)."""
        return cls(
            text=text,
            taint=TaintFlag.PRIVATE,
            source=source,
            data_class=DataClass.LOCAL_ONLY,
        )

    @classmethod
    def chat(cls, text: str, source: str | None = None) -> Content:
        """Convenience constructor for user chat messages."""
        return cls(text=text, taint=TaintFlag.NONE, source=source, data_class=DataClass.CHAT)

    @classmethod
    def private(cls, text: str, source: str | None = None) -> Content:
        """Convenience constructor for private content (alias for local_only)."""
        return cls.local_only(text=text, source=source)

    @classmethod
    def untrusted(cls, text: str, source: str | None = None) -> Content:
        """Convenience constructor for untrusted content."""
        return cls(text=text, taint=TaintFlag.UNTRUSTED, source=source, data_class=DataClass.PUBLIC)

    @classmethod
    def public(cls, text: str, source: str | None = None) -> Content:
        """Convenience constructor for clean public content."""
        return cls(text=text, taint=TaintFlag.NONE, source=source, data_class=DataClass.PUBLIC)


def combine_taint(*contents: Content) -> TaintFlag:
    """Return the union of taint flags from all given Content objects."""
    result = TaintFlag.NONE
    for c in contents:
        result |= c.taint
    return result


def combine_data_class(*contents: Content) -> DataClass:
    """Return the highest-ranking DataClass from all given Content objects."""
    highest = DataClass.PUBLIC
    for c in contents:
        dc = c.get_data_class()
        if dc.rank > highest.rank:
            highest = dc
    return highest
