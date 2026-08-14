"""spaCy NER layer.

This is the *recall* layer: it finds people and organisations that no rule
anticipated.  It is also the noisiest, so every candidate is filtered through
the same :func:`is_plausible_person` predicate the rules use, and entities are
emitted with a lower score than deterministic matches so the resolver prefers
pattern hits on overlap.

The layer is optional.  If spaCy or the model is not installed the pipeline logs
once and continues rules-only, which is what the "slim" container image does.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from functools import lru_cache

from ..config import RedactionConfig
from ..types import Entity, PIIType
from .persons import is_plausible_person

log = logging.getLogger(__name__)

#: spaCy's transformer-free English models are trained on OntoNotes; these are
#: the labels worth mapping.  GPE/LOC/FAC are *not* mapped to ADDRESS because a
#: bare city name is not a mailing address and redacting it destroys context.
_LABEL_MAP = {
    "PERSON": PIIType.PERSON,
    "ORG": PIIType.ORGANIZATION,
}

#: spaCy chokes on very long strings; process in chunks below its default limit.
_MAX_CHARS = 90_000


@lru_cache(maxsize=4)
def _load(model: str):
    import spacy  # imported lazily so the package installs without spaCy

    nlp = spacy.load(model, exclude=["lemmatizer", "textcat"])
    nlp.max_length = _MAX_CHARS + 10_000
    return nlp


class SpacyNERRecognizer:
    name = "spacy_ner"
    types = (PIIType.PERSON, PIIType.ORGANIZATION)

    def __init__(self) -> None:
        self._warned = False

    def analyze(self, text: str, config: RedactionConfig) -> Iterable[Entity]:
        if not config.spacy_model:
            return
        if not (config.is_enabled(PIIType.PERSON) or config.is_enabled(PIIType.ORGANIZATION)):
            return
        try:
            nlp = _load(config.spacy_model)
        except Exception as exc:  # pragma: no cover - environment dependent
            if not self._warned:
                log.warning("spaCy model %r unavailable (%s); running rules-only",
                            config.spacy_model, exc)
                self._warned = True
            return

        for offset, chunk in _chunks(text):
            for ent in nlp(chunk).ents:
                pii_type = _LABEL_MAP.get(ent.label_)
                if pii_type is None or not config.is_enabled(pii_type):
                    continue
                if pii_type is PIIType.ORGANIZATION and not config.ner_organizations:
                    continue
                value = ent.text.strip()
                if not value:
                    continue
                start = offset + ent.start_char + (len(ent.text) - len(ent.text.lstrip()))
                if pii_type is PIIType.PERSON:
                    if not is_plausible_person(value):
                        continue
                    score = 0.6
                else:
                    if not _plausible_org(value) or config.is_allowlisted(value):
                        continue
                    score = 0.5
                yield Entity(
                    type=pii_type,
                    start=start,
                    end=start + len(value),
                    text=value,
                    recognizer=self.name,
                    score=score,
                    notes=ent.label_,
                )


def _plausible_org(value: str) -> bool:
    tokens = value.split()
    if not 2 <= len(tokens) <= 8:
        return False
    if any(ch.isdigit() for ch in value):
        return False
    return sum(t[0].isupper() for t in tokens if t) >= 2


def _chunks(text: str) -> Iterable[tuple[int, str]]:
    """Split on paragraph boundaries so entity spans are never cut in half."""
    if len(text) <= _MAX_CHARS:
        yield 0, text
        return
    pos = 0
    while pos < len(text):
        end = min(pos + _MAX_CHARS, len(text))
        if end < len(text):
            split = text.rfind("\n", pos + _MAX_CHARS // 2, end)
            if split > pos:
                end = split
        yield pos, text[pos:end]
        pos = end
