"""Rule-based person-name recognizer.

Statistical NER alone is unreliable on this corpus (see EVALUATION.md): the
small English model tags Indian locality names as ``PERSON`` and splits
three-part names.  These rules find names via the structures a filing actually
uses -- honorifics, designation columns and contact labels -- at high precision,
and the resulting names then seed the propagation pass that provides recall.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..config import RedactionConfig
from ..lexicons import (
    DESIGNATIONS,
    HONORIFICS,
    PERSON_LABELS,
    PERSON_STOP_PHRASES,
    PERSON_STOP_TOKENS,
)
from ..types import Entity, PIIType

#: One name token: "Kushal", "D'Souza", "Rama-Krishna", or an initial "K.".
_TOKEN = r"(?:[A-Z][a-z]+(?:['’\-][A-Z]?[a-z]+)*|[A-Z]\.)"
_GAP = r"(?:[^\S\n]+|\n(?!\s*\n))"
#: A full name: 2-4 tokens.  Single tokens are handled only by propagation,
#: where we already know the token belongs to a confirmed person.
_NAME = rf"{_TOKEN}(?:{_GAP}{_TOKEN}){{1,3}}"
_NAME_CAPS = r"[A-Z]{2,}(?:[^\S\n]+[A-Z]{2,}){1,3}"

_HONORIFIC_ALT = "|".join(HONORIFICS)
_DESIGNATION_ALT = "|".join(re.escape(d) for d in sorted(DESIGNATIONS, key=len, reverse=True))
_LABEL_ALT = "|".join(re.escape(s) for s in sorted(PERSON_LABELS, key=len, reverse=True))

_RULES: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "honorific",
        re.compile(rf"\b(?:{_HONORIFIC_ALT})\.?\s+(?P<name>{_NAME})"),
        0.95,
    ),
    (
        "label",
        re.compile(rf"(?:{_LABEL_ALT})\s*[:\-–]?\s*(?P<name>{_NAME})"),
        0.9,
    ),
    (
        # "Contact person: Lokesh Shah/ Soumavo Sarkar" -- filings routinely list
        # two contacts behind one label; the rule above only sees the first.
        "label_second",
        re.compile(
            rf"(?:{_LABEL_ALT})\s*[:\-–]?\s*{_NAME}\s*[/,&]\s*(?:and\s+)?(?P<name>{_NAME})"
        ),
        0.85,
    ),
    (
        "designation_after",
        re.compile(
            rf"(?P<name>{_NAME})\s*(?:,|\n|is\s+our|is\s+the|–|-|:)?\s*"
            rf"(?:{_DESIGNATION_ALT})\b"
        ),
        0.85,
    ),
    (
        "designation_before",
        re.compile(rf"(?:{_DESIGNATION_ALT})\s*(?:,|:|–|-|\n)\s*(?P<name>{_NAME})"),
        0.75,
    ),
)

_PROMOTER_LIST = re.compile(
    r"(?:OUR\s+PROMOTERS?|PROMOTERS?\s+OF\s+OUR\s+COMPANY)\s*:?\s*(?P<body>[^\n]{0,900})",
    re.IGNORECASE,
)
_CAPS_NAME = re.compile(_NAME_CAPS)

#: Tokens that mark a caps-list entry as an entity rather than a human.
_ENTITY_MARKERS = ("TRUST", "LIMITED", "LTD", "LLP", "PRIVATE", "COMPANY", "FUND")


#: A well-formed name token: "Kushal", "D'Souza", "Rama-Krishna" or "N." .
_TITLE_TOKEN = re.compile(r"[A-Z][a-z]+(?:['’\-][A-Z]?[a-z]+)*\.?$")
_INITIAL_TOKEN = re.compile(r"[A-Z]\.?$")
_CAPS_TOKEN = re.compile(r"[A-Z]{2,}$")


def is_plausible_person(name: str) -> bool:
    """Reject candidates that are places, jargon or org fragments.

    This single predicate is where person precision is won or lost; both the
    rule layer and the NER layer route through it.  The shape test is what
    removes the bulk of spaCy's mistakes on this corpus -- "Non-GAAP Measures",
    "Gopal BO", "Bidder's DP ID" all fail it.
    """
    cleaned = " ".join(name.split())
    if len(cleaned) < 5:
        return False
    if cleaned.lower() in PERSON_STOP_PHRASES:
        return False
    tokens = cleaned.split()
    if not 2 <= len(tokens) <= 4:
        return False
    # "Bill Bill", "Gopal Gopal" -- a repeated token is a table artefact.
    if len({t.lower().strip(".") for t in tokens}) == 1:
        return False

    for tok in tokens:
        bare = tok.strip(".,'’-").lower()
        if not bare or bare in PERSON_STOP_TOKENS:
            return False

    # Names are either consistently Title Case (with optional initials) or, in
    # headings and promoter lists, consistently ALL CAPS.  Anything mixed is
    # almost always a fragment of a heading.
    title_shaped = all(
        _TITLE_TOKEN.match(t) or _INITIAL_TOKEN.match(t) for t in tokens
    ) and any(_TITLE_TOKEN.match(t) for t in tokens)
    caps_shaped = all(_CAPS_TOKEN.match(t.rstrip(".")) for t in tokens)
    return title_shaped or caps_shaped


def longest_plausible_person(value: str) -> str | None:
    """Trim trailing tokens until what remains is a plausible name.

    The name pattern is greedy and takes up to four title-case tokens, so in a
    run-together table cell -- "Contact Person: Cherag Gyara Website: ..." with
    the label stripped -- it grabs the following field's words too.  Rejecting
    the whole match would lose a real name; trimming from the right recovers it.
    """
    tokens = list(re.finditer(r"\S+", value))
    for end in range(len(tokens), 1, -1):
        candidate = value[: tokens[end - 1].end()]
        if is_plausible_person(candidate):
            return candidate
    return None


class PersonRuleRecognizer:
    name = "person_rules"
    types = (PIIType.PERSON,)

    def analyze(self, text: str, config: RedactionConfig) -> Iterable[Entity]:
        if not config.is_enabled(PIIType.PERSON):
            return
        seen: set[tuple[int, int]] = set()

        for rule_name, pattern, score in _RULES:
            for m in pattern.finditer(text):
                start = m.start("name")
                value = longest_plausible_person(text[start : m.end("name")])
                if value is None:
                    continue
                end = start + len(value)
                if (start, end) in seen:
                    continue
                seen.add((start, end))
                yield Entity(
                    type=PIIType.PERSON,
                    start=start,
                    end=end,
                    text=value,
                    recognizer=self.name,
                    score=score,
                    notes=rule_name,
                )

        yield from self._promoter_lists(text, seen)

    def _promoter_lists(self, text: str, seen: set[tuple[int, int]]) -> Iterable[Entity]:
        """``OUR PROMOTERS: A, B, C AND D PRIVATE LIMITED`` -- an all-caps run
        where the human entries must be separated from the trusts and companies.
        """
        for block in _PROMOTER_LIST.finditer(text):
            body_start = block.start("body")
            for m in _CAPS_NAME.finditer(block.group("body")):
                value = m.group(0)
                if any(marker in value for marker in _ENTITY_MARKERS):
                    continue
                if not is_plausible_person(value.title()):
                    continue
                start = body_start + m.start()
                end = start + len(value)
                if (start, end) in seen:
                    continue
                seen.add((start, end))
                yield Entity(
                    type=PIIType.PERSON,
                    start=start,
                    end=end,
                    text=value,
                    recognizer=self.name,
                    score=0.9,
                    notes="promoter_list",
                )
