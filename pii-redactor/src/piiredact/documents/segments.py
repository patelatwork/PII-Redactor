"""Whole-document analysis with per-segment application.

Documents are a list of text segments (paragraphs, table cells, header lines).
Analysing each in isolation would cripple the propagation pass, which needs to
see the confident mentions from page 1 before it can sweep page 90.  So we join
every segment into one string, analyse that once, and then split the resulting
entities back out into per-segment coordinates.

The join uses a blank line, which no recognizer is allowed to match across (see
the ``_GAP`` patterns in the recognizers), so no entity ever straddles two
segments.
"""

from __future__ import annotations

from bisect import bisect_right

from ..redactor import Redactor
from ..types import Entity

SEPARATOR = "\n\n"


class SegmentedText:
    """Join/split bookkeeping between segments and one analysable string."""

    def __init__(self, segments: list[str]) -> None:
        self.segments = segments
        self.starts: list[int] = []
        pos = 0
        for segment in segments:
            self.starts.append(pos)
            pos += len(segment) + len(SEPARATOR)
        self.joined = SEPARATOR.join(segments)

    def bucket(self, entities: list[Entity]) -> list[list[Entity]]:
        """Group entities by segment, rebased to segment-local offsets."""
        buckets: list[list[Entity]] = [[] for _ in self.segments]
        for entity in entities:
            idx = bisect_right(self.starts, entity.start) - 1
            if idx < 0:
                continue
            base = self.starts[idx]
            local_start = entity.start - base
            local_end = entity.end - base
            if local_end > len(self.segments[idx]):
                # Straddles the separator; should not happen, but drop rather
                # than corrupt the document.
                continue
            buckets[idx].append(
                Entity(
                    type=entity.type,
                    start=local_start,
                    end=local_end,
                    text=entity.text,
                    recognizer=entity.recognizer,
                    score=entity.score,
                    notes=entity.notes,
                )
            )
        return buckets


def analyze_segments(
    redactor: Redactor, segments: list[str]
) -> tuple[list[Entity], list[list[Entity]]]:
    """Analyse all segments together.

    Returns the document-level entity list (for reporting) and the same
    entities bucketed per segment (for substitution).
    """
    segmented = SegmentedText(segments)
    entities = redactor.analyze(segmented.joined)
    redactor.surrogates.prime(entities)
    return entities, segmented.bucket(entities)
