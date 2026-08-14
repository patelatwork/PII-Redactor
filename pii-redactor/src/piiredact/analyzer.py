"""The detection pipeline: text in, resolved PII entities out."""

from __future__ import annotations

import logging

from .config import RedactionConfig
from .recognizers import RecognizerRegistry, default_registry, propagate
from .resolution import dedupe, resolve_overlaps
from .types import Entity

log = logging.getLogger(__name__)


class Analyzer:
    """Runs every recognizer, then the propagation pass, then resolution.

    The two-pass design matters: propagation needs the whole document's
    confident detections before it can sweep, so ``analyze`` is called on the
    full concatenated text even when the caller later applies the results
    paragraph by paragraph.
    """

    def __init__(
        self,
        config: RedactionConfig | None = None,
        registry: RecognizerRegistry | None = None,
    ) -> None:
        self.config = config or RedactionConfig()
        self.registry = registry or default_registry()

    def analyze(self, text: str) -> list[Entity]:
        entities: list[Entity] = []
        for recognizer in self.registry:
            try:
                found = list(recognizer.analyze(text, self.config))
            except Exception:  # a bad rule must not take down a batch job
                log.exception("recognizer %r failed; skipping", recognizer.name)
                continue
            log.debug("%s -> %d candidates", recognizer.name, len(found))
            entities.extend(found)

        entities.extend(self._configured_denylist(text))

        # Resolve once *before* propagating so that only detections which
        # actually survive contention seed the second pass.  Without this a
        # building name that NER mislabels as a PERSON inside an address span
        # gets propagated across the whole document even though the address
        # would have won the overlap.
        seeds = resolve_overlaps(dedupe(entities), min_score=self.config.min_score)
        entities.extend(propagate(text, seeds, self.config))

        resolved = resolve_overlaps(dedupe(entities), min_score=self.config.min_score)
        log.info("resolved %d entities from %d candidates", len(resolved), len(entities))
        return resolved

    def _configured_denylist(self, text: str) -> list[Entity]:
        """Literal strings an operator forced into scope via config."""
        import re

        out: list[Entity] = []
        for pii_type, values in self.config.extra_deny.items():
            if not self.config.is_enabled(pii_type):
                continue
            for value in values:
                for m in re.finditer(rf"(?<!\w){re.escape(value)}(?!\w)", text):
                    out.append(
                        Entity(
                            type=pii_type,
                            start=m.start(),
                            end=m.end(),
                            text=m.group(0),
                            recognizer="config_denylist",
                            score=1.0,
                        )
                    )
        return out
