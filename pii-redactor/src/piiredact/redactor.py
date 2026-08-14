"""Top-level redaction API.

``Redactor`` owns an :class:`~piiredact.analyzer.Analyzer` and a
:class:`~piiredact.surrogates.SurrogateFactory` and applies one to the output of
the other.  It is the only object callers (CLI, HTTP service, tests) need.
"""

from __future__ import annotations

from collections import Counter

from .analyzer import Analyzer
from .config import RedactionConfig
from .surrogates import SurrogateFactory
from .types import Entity, PIIType, RedactionResult


class Redactor:
    def __init__(self, config: RedactionConfig | None = None) -> None:
        self.config = config or RedactionConfig()
        self.analyzer = Analyzer(self.config)
        self.surrogates = SurrogateFactory(self.config)

    # -- analysis --------------------------------------------------------

    def analyze(self, text: str) -> list[Entity]:
        return self.analyzer.analyze(text)

    # -- redaction -------------------------------------------------------

    def redact(self, text: str) -> RedactionResult:
        """Analyse and rewrite a single string."""
        entities = self.analyze(text)
        self.surrogates.prime(entities)
        return RedactionResult(
            text=self.apply(text, entities),
            entities=entities,
            mapping=self.surrogates.mapping,
        )

    def apply(self, text: str, entities: list[Entity]) -> str:
        """Substitute pre-computed entities into ``text``.

        Kept separate from :meth:`redact` because document backends analyse the
        whole document once (so propagation sees everything) and then apply the
        results to each paragraph independently.
        """
        pieces: list[str] = []
        cursor = 0
        for entity in sorted(entities, key=lambda e: e.start):
            if entity.start < cursor:  # defensive: resolver should prevent this
                continue
            pieces.append(text[cursor : entity.start])
            pieces.append(self.surrogates.for_entity(entity))
            cursor = entity.end
        pieces.append(text[cursor:])
        return "".join(pieces)

    # -- reporting -------------------------------------------------------

    @staticmethod
    def summarise(entities: list[Entity]) -> dict[str, int]:
        counts = Counter(e.type.value for e in entities)
        return {t.value: counts.get(t.value, 0) for t in PIIType if counts.get(t.value)}

    def mapping_rows(self) -> list[dict[str, str]]:
        return [
            {"type": pii_type, "original": original, "surrogate": surrogate}
            for (pii_type, original), surrogate in sorted(self.surrogates.mapping.items())
        ]
