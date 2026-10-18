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


@dataclass
class Content:
    """A piece of text with associated taint flags and an optional source label.

    Args:
        text: The raw text payload.
        taint: Combination of ``TaintFlag`` values for this content.
        source: Optional human-readable label (URL, file path, etc.)
                for attribution and debugging. Never used in routing decisions.
    """

    text: str
    taint: TaintFlag = TaintFlag.NONE
    source: str | None = None

    # Tag name used to wrap untrusted content before it enters the model
    UNTRUSTED_TAG: ClassVar[str] = "untrusted_content"

    def is_private(self) -> bool:
        """Return True if this content carries the PRIVATE flag."""
        return bool(self.taint & TaintFlag.PRIVATE)

    def is_untrusted(self) -> bool:
        """Return True if this content carries the UNTRUSTED flag."""
        return bool(self.taint & TaintFlag.UNTRUSTED)

    def is_clean(self) -> bool:
        """Return True if this content has no taint flags."""
        return self.taint == TaintFlag.NONE

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
        with a newline separator. Use this when combining outputs from
        multiple sources in a single context block.
        """
        return Content(
            text=self.text + "\n" + other.text,
            taint=self.taint | other.taint,
            source=self.source,  # keep the first source as the primary label
        )

    @classmethod
    def private(cls, text: str, source: str | None = None) -> Content:
        """Convenience constructor for private content."""
        return cls(text=text, taint=TaintFlag.PRIVATE, source=source)

    @classmethod
    def untrusted(cls, text: str, source: str | None = None) -> Content:
        """Convenience constructor for untrusted content."""
        return cls(text=text, taint=TaintFlag.UNTRUSTED, source=source)

    @classmethod
    def public(cls, text: str, source: str | None = None) -> Content:
        """Convenience constructor for clean public content."""
        return cls(text=text, taint=TaintFlag.NONE, source=source)


def combine_taint(*contents: Content) -> TaintFlag:
    """Return the union of taint flags from all given Content objects."""
    result = TaintFlag.NONE
    for c in contents:
        result |= c.taint
    return result
