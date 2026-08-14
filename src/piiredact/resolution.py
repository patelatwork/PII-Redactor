"""Turn a bag of possibly-overlapping detections into one clean, ordered set.

Multiple recognizers legitimately fire on the same characters -- an ADDRESS
contains a city that NER calls an ORG, an EMAIL contains something that looks
like a URL.  Substitution needs disjoint spans, so we pick a winner per region.

Ranking, in order:
1. **Type priority** -- deterministic identifiers beat inferred names, and
   container types (ADDRESS) beat what they contain.
2. **Span length** -- prefer the fuller name over a fragment of it.
3. **Score** -- prefer the more confident recognizer.
4. **Position** -- for a stable, reproducible tie-break.
"""

from __future__ import annotations

from .types import Entity


def _rank(entity: Entity) -> tuple[int, int, float, int]:
    return (entity.priority, entity.length, entity.score, -entity.start)


def resolve_overlaps(entities: list[Entity], min_score: float = 0.0) -> list[Entity]:
    """Return non-overlapping entities, sorted by start offset."""
    candidates = [e for e in entities if e.score >= min_score]
    candidates.sort(key=_rank, reverse=True)

    kept: list[Entity] = []
    # Number of characters is small enough per chunk that a linear scan over
    # kept spans is cheaper than an interval tree, and keeps this readable.
    occupied: list[tuple[int, int]] = []
    for entity in candidates:
        if any(entity.start < end and start < entity.end for start, end in occupied):
            continue
        kept.append(entity)
        occupied.append((entity.start, entity.end))

    kept.sort(key=lambda e: e.start)
    return kept


def dedupe(entities: list[Entity]) -> list[Entity]:
    """Drop exact duplicate spans, keeping the highest-ranked one."""
    best: dict[tuple[int, int], Entity] = {}
    for entity in entities:
        key = (entity.start, entity.end)
        current = best.get(key)
        if current is None or _rank(entity) > _rank(current):
            best[key] = entity
    return sorted(best.values(), key=lambda e: e.start)
