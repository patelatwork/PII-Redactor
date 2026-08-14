"""Recognizer registry.

To add a PII type: write a recognizer exposing ``name``, ``types`` and
``analyze(text, config)``, add its type to :class:`~piiredact.types.PIIType`,
give it a priority in ``TYPE_PRIORITY``, teach
:mod:`piiredact.surrogates` how to fake it, then append it below.
"""

from __future__ import annotations

from .addresses import AddressRecognizer
from .base import Recognizer, RecognizerRegistry, RegexRecognizer
from .ner import SpacyNERRecognizer
from .organizations import OrganizationRecognizer
from .patterns import PATTERN_RECOGNIZERS
from .persons import PersonRuleRecognizer
from .propagation import propagate

__all__ = [
    "Recognizer",
    "RecognizerRegistry",
    "RegexRecognizer",
    "default_registry",
    "propagate",
]


def default_registry() -> RecognizerRegistry:
    """The recognizers used unless a caller supplies its own set."""
    return RecognizerRegistry(
        [
            *PATTERN_RECOGNIZERS,
            AddressRecognizer(),
            OrganizationRecognizer(),
            PersonRuleRecognizer(),
            SpacyNERRecognizer(),
        ]
    )
