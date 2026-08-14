"""Second pass: document-wide propagation of confirmed names.

Rationale: a person or company is usually introduced once in a high-signal
context ("Sarthak Malvadkar is our Company Secretary…") and then referred to
dozens of times in running prose where no local cue exists.  Detecting the first
mention is a precision problem; the rest is a string-matching problem.

So pass 1 collects only confident PERSON/ORGANIZATION detections, and pass 2
sweeps the whole document for those names plus their predictable short forms
("Kushal Subbayya Hegde" → "Kushal Hegde"; "KSH International Limited" →
"KSH International").  This is where most of the recall comes from.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from ..config import RedactionConfig
from ..lexicons import ORG_SUFFIXES, PERSON_STOP_PHRASES, PERSON_STOP_TOKENS
from ..types import Entity, PIIType

#: Minimum pass-1 score for a detection to be trusted enough to propagate.
PERSON_SEED_SCORE = 0.75
ORG_SEED_SCORE = 0.8

_SUFFIX_TOKENS = frozenset(
    tok.lower().strip(".") for s in ORG_SUFFIXES for tok in s.split()
)

_WS = re.compile(r"\s+")

#: Word edges for name matching.  A plain ``\b`` would happily match inside an
#: e-mail address or hostname, so ``.`` and ``@`` are excluded -- but only when
#: they are *joined* to more text.  A sentence-final full stop must still count
#: as an edge, otherwise every name that ends a sentence is missed.
_LEFT_EDGE = r"(?<![\w@])(?<!\w\.)"
_RIGHT_EDGE = r"(?!\w)(?![.@]\w)"


def normalise(value: str) -> str:
    return _WS.sub(" ", value).strip()


class PersonIdentityIndex:
    """Maps any mention of a person to a stable canonical identity.

    The canonical form is the longest confirmed spelling of the name.  A shorter
    mention resolves to it when its tokens are a subset of exactly one
    canonical; if several canonicals match (common with shared family names such
    as "Kushal Hegde"), the mention becomes its own identity so the output stays
    deterministic -- redaction is still complete, only the cross-reference
    between long and short form is lost.
    """

    def __init__(self, canonicals: Iterable[str]) -> None:
        self._canonicals: list[tuple[frozenset[str], str]] = []
        for name in sorted({normalise(c) for c in canonicals}, key=len, reverse=True):
            keys = frozenset(t.lower() for t in name.split())
            # A short form already covered by a longer name is not a separate
            # identity -- keeping it would make every short mention "ambiguous"
            # against its own full name.
            if any(keys <= existing for existing, _ in self._canonicals):
                continue
            self._canonicals.append((keys, name))

    def resolve(self, mention: str) -> str:
        mention = normalise(mention)
        tokens = frozenset(t.lower() for t in mention.split())
        matches = [name for keys, name in self._canonicals if tokens <= keys]
        if len(matches) == 1:
            return matches[0]
        return mention

    def token_positions(self, canonical: str, mention: str) -> list[int]:
        """Which slots of ``canonical`` the mention's tokens occupy.

        Used to render a short mention from the full fake persona, so
        "Kushal Hegde" becomes "Arjun Iyer" when the full name became
        "Arjun Ramesh Iyer".
        """
        canon_tokens = [t.lower() for t in normalise(canonical).split()]
        positions: list[int] = []
        cursor = 0
        for tok in normalise(mention).split():
            try:
                idx = canon_tokens.index(tok.lower(), cursor)
            except ValueError:
                return list(range(len(normalise(mention).split())))
            positions.append(idx)
            cursor = idx + 1
        return positions


def person_variants(name: str) -> set[str]:
    """Short forms of a full name that are still identifying."""
    tokens = normalise(name).split()
    variants = {" ".join(tokens)}
    if len(tokens) >= 3:
        variants.add(f"{tokens[0]} {tokens[-1]}")
        for i in range(len(tokens) - 1):
            variants.add(f"{tokens[i]} {tokens[i + 1]}")
    return {v for v in variants if _variant_is_safe(v)}


#: Capitalised words that head plenty of company names but are also ordinary
#: English.  Redacting them as a standalone short form ("Link", "Care", "First")
#: would fire all over the running text, so single-token propagation skips them.
_AMBIGUOUS_ORG_HEADS = frozenset(
    [
        "link", "care", "first", "new", "global", "national", "international",
        "indian", "india", "central", "general", "standard", "united",
        "american", "royal", "prime", "smart", "power", "energy", "solar",
        "green", "blue", "metro", "city", "state", "union", "federal",
        "total", "core", "alpha", "beta", "future", "modern", "premier",
        "unique", "select", "quality", "value", "master", "super", "mega",
    ]
)


def org_variants(name: str) -> set[str]:
    """The full company name, the name without its legal suffix, and -- when the
    head word is distinctive enough -- the bare short form ("Nuvama")."""
    tokens = normalise(name).split()
    variants = {" ".join(tokens)}
    trimmed = list(tokens)
    while trimmed and trimmed[-1].lower().strip(".,") in _SUFFIX_TOKENS:
        trimmed.pop()
    if len(trimmed) >= 2:
        variants.add(" ".join(trimmed))

    multi = {v for v in variants if len(v) >= 6 and len(v.split()) >= 2}
    head = trimmed[0].strip(".,") if trimmed else ""
    if _is_distinctive_head(head):
        multi.add(head)
    return multi


def _is_distinctive_head(head: str) -> bool:
    """Is this first word specific enough to stand for the company on its own?

    Either a long-enough distinctive word ("Nuvama", "Waterloo") or an all-caps
    house acronym ("KSH", "MUFG"), which is how filings refer to the issuer
    throughout the running text.
    """
    if not head.isalpha() or head.lower() in _AMBIGUOUS_ORG_HEADS:
        return False
    if head.lower() in PERSON_STOP_TOKENS:
        return False
    return len(head) >= 5 or (head.isupper() and len(head) >= 3)


def surname_patterns(names: Iterable[str]) -> set[str]:
    """Regexes matching "<Unknown first name> <known surname>".

    Filings introduce minor parties once, in prose, with no designation nearby
    ("...shares transferred by Vijay Hegde"), so no rule fires and NER often
    misses them.  But the *surname* is already confirmed from the promoters and
    directors, and a document-specific surname preceded by a capitalised word is
    a person with high reliability.
    """
    surnames: set[str] = set()
    for name in names:
        tokens = normalise(name).split()
        if len(tokens) < 2:
            continue
        surname = tokens[-1].strip(".,")
        if len(surname) >= 4 and surname.isalpha() and surname.lower() not in PERSON_STOP_TOKENS:
            surnames.add(surname)
    return surnames


def _variant_is_safe(variant: str) -> bool:
    if len(variant) < 6 or len(variant.split()) < 2:
        return False
    if variant.lower() in PERSON_STOP_PHRASES:
        return False
    return not any(t.strip(".,").lower() in PERSON_STOP_TOKENS for t in variant.split())


def _variant_pattern(variants: Iterable[str]) -> re.Pattern[str] | None:
    """One alternation over all variants, tolerant of line wraps and case.

    Sorted longest-first so the regex engine prefers the fullest match.
    """
    parts = []
    for variant in sorted(set(variants), key=len, reverse=True):
        tokens = [re.escape(t) for t in variant.split()]
        parts.append(r"[^\S\n]*\n?[^\S\n]*".join(tokens))
    if not parts:
        return None
    return re.compile(rf"{_LEFT_EDGE}(?:{'|'.join(parts)}){_RIGHT_EDGE}", re.IGNORECASE)


def propagate(text: str, entities: list[Entity], config: RedactionConfig) -> list[Entity]:
    """Return additional entities found by sweeping confirmed names over ``text``."""
    if not config.enable_propagation:
        return []

    seeds: dict[PIIType, set[str]] = defaultdict(set)
    for ent in entities:
        if ent.type is PIIType.PERSON and ent.score >= PERSON_SEED_SCORE:
            seeds[PIIType.PERSON].add(normalise(ent.text).title() if ent.text.isupper()
                                      else normalise(ent.text))
        elif ent.type is PIIType.ORGANIZATION and ent.score >= ORG_SEED_SCORE:
            seeds[PIIType.ORGANIZATION].add(normalise(ent.text))

    variant_to_type: dict[str, PIIType] = {}
    for name in seeds[PIIType.PERSON]:
        for variant in person_variants(name):
            variant_to_type.setdefault(variant, PIIType.PERSON)
    for name in seeds[PIIType.ORGANIZATION]:
        if config.is_allowlisted(name):
            continue
        for variant in org_variants(name):
            variant_to_type.setdefault(variant, PIIType.ORGANIZATION)

    pattern = _variant_pattern(variant_to_type)
    if pattern is None:
        return []

    found = _sweep(text, pattern, variant_to_type, config)
    found.extend(_sweep_surnames(text, seeds[PIIType.PERSON], config))
    return found


def _sweep(
    text: str,
    pattern: re.Pattern[str],
    variant_to_type: dict[str, PIIType],
    config: RedactionConfig,
) -> list[Entity]:

    # The pattern matches case-insensitively and across line wraps, so resolve
    # each hit back to its variant through a case-folded, whitespace-normalised
    # lookup built once.
    by_lower = {k.lower(): v for k, v in variant_to_type.items()}

    found: list[Entity] = []
    for m in pattern.finditer(text):
        value = m.group(0)
        key = normalise(value)
        pii_type = by_lower.get(key.lower())
        if pii_type is None or not config.is_enabled(pii_type):
            continue
        if config.is_allowlisted(key):
            continue
        found.append(
            Entity(
                type=pii_type,
                start=m.start(),
                end=m.end(),
                text=value,
                recognizer="propagation",
                score=0.7,
                notes="second-pass",
            )
        )
    return found


def _sweep_surnames(
    text: str, person_seeds: set[str], config: RedactionConfig
) -> list[Entity]:
    """Find "<capitalised word> <confirmed surname>" pairs."""
    if not config.is_enabled(PIIType.PERSON):
        return []
    surnames = surname_patterns(person_seeds)
    if not surnames:
        return []
    alt = "|".join(re.escape(s) for s in sorted(surnames, key=len, reverse=True))
    pattern = re.compile(
        rf"{_LEFT_EDGE}(?P<first>[A-Z][a-z]{{2,}})[^\S\n]*\n?[^\S\n]*(?:{alt}){_RIGHT_EDGE}"
    )
    found: list[Entity] = []
    for m in pattern.finditer(text):
        if m.group("first").lower() in PERSON_STOP_TOKENS:
            continue
        found.append(
            Entity(
                type=PIIType.PERSON,
                start=m.start(),
                end=m.end(),
                text=m.group(0),
                recognizer="propagation",
                score=0.65,
                notes="known-surname",
            )
        )
    return found
