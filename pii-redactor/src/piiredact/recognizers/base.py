"""Recognizer protocol and the shared registry.

A recognizer is any object with a ``name``, the set of ``types`` it can emit,
and an ``analyze(text, config) -> Iterable[Entity]`` method.  That is the entire
extension point: to support a new PII type you write one recognizer and
register it.  See README, "Adding a new PII type".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from ..config import RedactionConfig
from ..types import Entity, PIIType


@runtime_checkable
class Recognizer(Protocol):
    name: str
    types: tuple[PIIType, ...]

    def analyze(self, text: str, config: RedactionConfig) -> Iterable[Entity]: ...


class RegexRecognizer:
    """Convenience base for recognizers that are one compiled pattern.

    Subclasses may override :meth:`validate` to add a checksum or context test
    (Luhn for cards, octet ranges for IPs, ...), and :meth:`span` to narrow the
    match to a named group.
    """

    name: str = "regex"
    types: tuple[PIIType, ...] = ()
    pattern: re.Pattern[str]
    pii_type: PIIType
    score: float = 0.95
    group: str | int = 0

    def analyze(self, text: str, config: RedactionConfig) -> Iterable[Entity]:
        if not config.is_enabled(self.pii_type):
            return
        for match in self.pattern.finditer(text):
            start, end = match.span(self.group)
            value = text[start:end]
            # Trim trailing punctuation/whitespace that the pattern may have
            # swallowed; keeps surrogate substitution clean.
            stripped = value.rstrip(" \t\r\n.,;:)")
            end -= len(value) - len(stripped)
            value = stripped
            if not value:
                continue
            if not self.validate(value, match, text):
                continue
            if config.is_allowlisted(value) or self.skip(value, config):
                continue
            yield Entity(
                type=self.pii_type,
                start=start,
                end=end,
                text=value,
                recognizer=self.name,
                score=self.score,
            )

    def validate(self, value: str, match: re.Match[str], text: str) -> bool:
        """Structural check: is this match really an instance of the type?"""
        return True

    def skip(self, value: str, config: RedactionConfig) -> bool:
        """Policy check: is this a real instance we nonetheless choose to keep?"""
        return False


class RecognizerRegistry:
    """Ordered collection of recognizers, run in sequence by the analyzer."""

    def __init__(self, recognizers: Iterable[Recognizer] = ()) -> None:
        self._recognizers: list[Recognizer] = list(recognizers)

    def register(self, recognizer: Recognizer) -> RecognizerRegistry:
        self._recognizers.append(recognizer)
        return self

    def __iter__(self):
        return iter(self._recognizers)

    def __len__(self) -> int:
        return len(self._recognizers)

    def names(self) -> list[str]:
        return [r.name for r in self._recognizers]
