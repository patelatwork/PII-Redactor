"""Organisation-name recognizer driven by legal-entity suffixes.

On corporate filings this beats statistical NER by a wide margin: company names
in a prospectus almost always terminate in a registered legal suffix
("… Limited", "… LLP", "… Family Trust"), so we anchor on the suffix and walk
left across the title-case run that precedes it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..config import RedactionConfig
from ..lexicons import ORG_SUFFIXES
from ..types import Entity, PIIType

#: Individual tokens that can terminate a company name.
_SUFFIX_TOKENS = frozenset(
    t.lower().strip(".,&") for s in ORG_SUFFIXES for t in s.split() if t not in {"&"}
)

# Longest suffix first so "Private Limited" wins over "Limited".  Upper-case
# spellings are listed explicitly rather than using re.IGNORECASE, because the
# surrounding name pattern relies on capitalisation to tell a company name from
# ordinary prose -- "OUR PROMOTERS: ... DHAULAGIRI FAMILY TRUST" must match, but
# "the family trust arrangement" must not.
_SUFFIX_ALT = "|".join(
    re.escape(s)
    for s in sorted({*ORG_SUFFIXES, *(s.upper() for s in ORG_SUFFIXES)}, key=len, reverse=True)
)

#: A name part: a capitalised word, an acronym, an ampersand, or a lowercase
#: connective that legitimately appears inside company names ("of", "and").
_NAME_PART = r"(?:[A-Z][A-Za-z0-9'’\-.]*|[A-Z]{2,}|&|of|and|the|for|in|de|von)"

#: Intra-name whitespace.  A single newline is allowed (names wrap in tables)
#: but a blank line ends the candidate, so names never glue across paragraphs.
_GAP = r"(?:[^\S\n]+|\n(?!\s*\n))+"

_ORG_PATTERN = re.compile(
    rf"\b(?P<org>(?:{_NAME_PART}{_GAP}){{0,7}}(?:{_SUFFIX_ALT}))(?![A-Za-z])"
)

#: Words that must not *start* an organisation name -- these are almost always
#: the tail of the preceding sentence rather than part of the name.
_LEADING_NOISE = frozenset(
    w.lower()
    for w in [
        "the", "of", "and", "for", "in", "our", "a", "an", "to", "by", "with",
        "at", "on", "from", "as", "is", "are", "was", "were", "be", "been",
        "this", "that", "these", "those", "its", "their", "his", "her",
        "such", "any", "all", "each", "other", "certain", "see", "including",
        "namely", "viz", "i.e", "e.g", "under", "pursuant", "means",
        # Field labels that precede a company name in a filing's tables.
        "company", "companies", "formerly", "offer", "promoter", "promoters",
        "corporate", "escrow", "sponsor", "syndicate", "registrar", "trusts",
        "auditors", "statutory", "collection", "refund", "issue", "banker",
        "bankers", "counsel", "name", "monitoring", "agency", "public",
    ]
)

#: Generic phrases that end in a suffix word but are not a specific company.
_GENERIC_ORGS = frozenset(
    s.lower()
    for s in [
        "limited", "ltd", "llp", "trust", "bank", "corporation", "inc",
        "private limited", "family trust", "chartered accountants",
        "the company limited", "a company limited", "public limited company",
        "scheduled bank", "the bank", "our bank", "escrow bank",
        "sponsor bank", "refund bank", "public issue bank",
        "limited liability partnership", "associates", "partners",
        "industries", "holdings", "ventures", "capital", "enterprises",
        "india limited", "bank limited", "india private limited",
    ]
)


class OrganizationRecognizer:
    name = "organization_suffix"
    types = (PIIType.ORGANIZATION,)

    def analyze(self, text: str, config: RedactionConfig) -> Iterable[Entity]:
        if not config.is_enabled(PIIType.ORGANIZATION):
            return
        for m in _ORG_PATTERN.finditer(text):
            for start, value in _split_candidates(m.start("org"), m.group("org")):
                tokens = value.split()
                # A bare suffix ("Limited") is not a company name.
                if len(tokens) < 2:
                    continue
                if " ".join(tokens).lower() in _GENERIC_ORGS:
                    continue
                if config.is_allowlisted(value):
                    continue
                score = 0.93 if len(tokens) >= 3 else 0.8
                yield Entity(
                    type=PIIType.ORGANIZATION,
                    start=start,
                    end=start + len(value),
                    text=value,
                    recognizer=self.name,
                    score=score,
                )


def _split_candidates(start: int, raw: str) -> list[tuple[int, str]]:
    """Break one greedy match into the company names it actually contains.

    The pattern walks left from a legal suffix and will happily cross a
    *previous* company name: "HDFC Bank Limited and ICICI Bank Limited", or the
    table artefact "Offer Escrow Collection Bank HDFC Bank Limited".  A legal
    suffix in the middle of a candidate always ends a name, so we cut there and
    emit each part -- which both narrows the spans and finds the extra company.
    """
    tokens = list(re.finditer(r"\S+", raw))
    segments: list[tuple[int, str]] = []
    seg_start = 0
    for i, token in enumerate(tokens):
        is_suffix = token.group(0).lower().strip(".,&") in _SUFFIX_TOKENS
        if is_suffix and i < len(tokens) - 1:
            # Multi-word suffixes ("Private Limited", "Family Trust") must not
            # be split in the middle.
            if tokens[i + 1].group(0).lower().strip(".,") in _SUFFIX_TOKENS:
                continue
            segments.append((seg_start, token.end()))
            seg_start = tokens[i + 1].start()
    segments.append((seg_start, len(raw)))

    out: list[tuple[int, str]] = []
    for lo, hi in segments:
        piece = _trim_leading_noise(raw[lo:hi])
        if piece is None:
            continue
        offset = lo + (hi - lo) - len(piece)
        piece, dropped = _drop_repeated_prefix(piece)
        out.append((start + offset + dropped, piece))
    return out


def _drop_repeated_prefix(value: str) -> tuple[str, int]:
    """Collapse "Nuvama Nuvama Wealth ..." / "ICICI Securities ICICI Securities ...".

    Table extraction repeats a cell's short label in front of its full value.
    Returns the trimmed name and how many characters were removed from the left.
    """
    tokens = list(re.finditer(r"\S+", value))
    for k in range(1, len(tokens) // 2 + 1):
        head = [t.group(0).lower() for t in tokens[:k]]
        if head == [t.group(0).lower() for t in tokens[k : 2 * k]]:
            cut = tokens[k].start()
            return value[cut:], cut
    return value, 0


def _trim_leading_noise(value: str) -> str | None:
    """Drop connectives the greedy match pulled in from the previous clause.

    Returns a suffix of ``value`` (never a re-joined copy) so the caller can
    recover the offset shift from the length difference alone.
    """
    remaining = value
    while True:
        head = re.match(r"(\S+)(\s+)", remaining)
        if head is None or head.group(1).lower().strip(".,") not in _LEADING_NOISE:
            break
        remaining = remaining[head.end() :]
    return remaining or None
