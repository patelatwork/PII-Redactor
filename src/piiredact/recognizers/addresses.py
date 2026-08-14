"""Physical / mailing address recognizer.

Addresses have no reliable left boundary, so we anchor on the part that *is*
unambiguous -- the postal tail -- and expand leftwards:

* **Indian tail**: ``<City> – 411 045 Maharashtra, India`` (6-digit PIN, often
  split ``411 045``, optionally followed by state and country).
* **Western tail**: ``… Street, Springfield, IL 62704``.

Expanding left stops at a paragraph break, a sentence end, a label such as
``Registered Office``, or a hard cap of 220 characters.  Boundary detection is
the weakest part of the pipeline and is reported as such in EVALUATION.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..config import RedactionConfig
from ..lexicons import ADDRESS_LABELS, INDIAN_STATES, STREET_TYPES
from ..types import Entity, PIIType

_STATE_ALT = "|".join(re.escape(s) for s in sorted(INDIAN_STATES, key=len, reverse=True))
_STREET_ALT = "|".join(re.escape(s) for s in sorted(STREET_TYPES, key=len, reverse=True))

#: A 6-digit PIN, which extraction may have wrapped mid-number ("400 \n025").
_PIN = r"(?<![\w,.])\d{3}\s{0,2}\d{3}(?!\w)"

#: Indian postal tail: a 6-digit PIN *confirmed* by geography.  A bare 6-digit
#: run is far too common in a prospectus to trust on its own, so one of two
#: confirmations is required:
#:
#: 1. a state and/or "India" follows the PIN, or
#: 2. the PIN closes the paragraph and is introduced by "<Place> – ", the
#:    standard Indian format.
#:
#: Case 2 exists because Word stores each line of an address block as its own
#: paragraph, so "…Baner Pune – 411 045" and "Maharashtra, India" arrive
#: separately and the first has no geography after the PIN at all.
_INDIA_TAIL = re.compile(
    rf"(?P<pin>{_PIN})(?:\s*,?\s*(?:{_STATE_ALT}))?\s*,?\s*India\b"
    rf"|(?P<pin2>{_PIN})\s*,?\s*(?:{_STATE_ALT})\b"
    # The separating dash must be spaced, as it is in "Pune – 410 501".  An
    # unspaced one belongs to an identifier: the engineer's registration number
    # "M-140388" is otherwise a perfect match for this rule.
    rf"|\s[-–]\s?(?P<pin3>{_PIN})[^\S\n]*(?=\n\s*\n|\Z)",
    re.IGNORECASE,
)

#: US-style tail: "City, ST 12345".
_US_TAIL = re.compile(r",\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")

_STREET_LINE = re.compile(rf"\b\d{{1,5}}[A-Za-z]?\s+(?:[A-Z][\w'.\-]*\s+){{0,5}}(?:{_STREET_ALT})\b")

#: An address line with no postal code at all -- the first line of a block that
#: Word split ("Gat No. 11/3, 11/4, 11/5, Village Birdewadi").  Three signals
#: must coincide, because on their own each is common in a filing's tables:
#: the line starts with a premises number, it names a premises type, and it is
#: comma-separated the way an address is.
_PREMISES_START = (
    r"(?:\d{1,5}[A-Za-z]?(?:\s?[/\-,]\s?\d{1,5}[A-Za-z]?)*"
    r"|Gat\s+No\.?|S\.\s?no\.?|Plot\s+No\.?|Survey\s+No\.?|Flat\s+No\.?)"
)
#: Deliberately excludes "Unit", "Floor", "Block", "Tower" and "Off": this
#: filing's capacity tables are full of rows like "2 Chakan Unit No. 2".
_PREMISES_WORDS = (
    r"Village|Gat|Plot|Survey|Flat|Bunglow|Bungalow|Apartment|Apartments"
    r"|Society|Residency|Marg|Wing|Building"
)
#: The line may open the paragraph or follow an introducing colon
#: ("His contact details are as set forth below: Gat No. 11/3, ...").
_PREMISES_OPEN = r"(?:^|(?<=:\s))"
_PREMISES_LINE = re.compile(
    rf"{_PREMISES_OPEN}{_PREMISES_START}\b[^\n]*?,[^\n]*?\b(?:{_PREMISES_WORDS})\b[^\n]*$"
    rf"|{_PREMISES_OPEN}{_PREMISES_START}\b[^\n]*?\b(?:{_PREMISES_WORDS})\b[^\n]*?,[^\n]*$",
    re.MULTILINE | re.IGNORECASE,
)

_LABEL_ALT = "|".join(re.escape(s) for s in sorted(ADDRESS_LABELS, key=len, reverse=True))
_LABEL_RE = re.compile(
    rf"(?:{_LABEL_ALT})[^\S\n]*(?:of our Company|of the Company)?[^\S\n]*"
    rf"(?:is\s+)?(?:located\s+)?(?:situated\s+)?(?:at\b)?[^\S\n]*[:\-–,]?[^\S\n]*",
    re.IGNORECASE,
)

_MAX_LEFT = 220

#: Left-boundary markers.  The rightmost one found in the lookback window wins,
#: so the address starts *after* whatever last ended: a paragraph, a sentence, a
#: field label, a company name, or a contact detail that belongs to a different
#: field.  Excluding phones and e-mails here is what stops an address span from
#: swallowing the "Email: … Telephone: …" line above it in a contact block.
_BOUNDARY = re.compile(
    r"\n\s*\n"                                              # paragraph break
    r"|[.;]\s+(?=[A-Z0-9])"                                 # sentence end
    r"|\b(?:Limited|Ltd\.?|LLP|Inc\.?|Corporation|Trust)\b[,\s]+"  # end of a company name
    r"|\S+@\S+\.\w+[\s,]+"                                  # after an e-mail
    r"|\+\s?\d[\d\s\-().]{6,}"                              # after a phone number
    r"|[:\t]"                                               # after a field label
    r"|\b[A-Za-z]{2,}\d{4,}\b[\s,]*"                        # after a registration number
    r"|(?<!\d)\d{6,8}(?:\s*\n\s*\d{1,2})?(?!\d)[\s,]*"      # after an id column (DIN)
    # Clauses that introduce an address in running prose.  Without these the
    # span grows leftwards through the whole sentence ("a company incorporated
    # on July 30, 1979 under the Companies Act, 1956 and having its Registered
    # Office at 11/3, ...").
    r"|\b(?:located|situated)\s+(?:at\s+)?"
    r"|\boffices?\s+(?:at|in)\s+"
    r"|\baddress\s*(?:is|of)?\s*[:\-–]?\s*"
    r"|\bnamely,?\s+"
    r"|\bsee\b",
    re.IGNORECASE,
)


class AddressRecognizer:
    name = "address"
    types = (PIIType.ADDRESS,)

    def analyze(self, text: str, config: RedactionConfig) -> Iterable[Entity]:
        if not config.is_enabled(PIIType.ADDRESS):
            return
        spans: list[tuple[int, int, float]] = []

        for m in _INDIA_TAIL.finditer(text):
            pin = re.sub(r"\s", "", m.group("pin") or m.group("pin2") or m.group("pin3"))
            # Indian PINs never start with 0; this also rejects "100 000" style
            # figures in financial tables.
            if len(pin) != 6 or not pin.isdigit() or pin[0] == "0":
                continue
            start = _expand_left(text, m.start())
            if start is None:
                continue
            spans.append((start, m.end(), 0.9))

        for m in _PREMISES_LINE.finditer(text):
            spans.append((m.start(), m.end(), 0.7))

        for m in _US_TAIL.finditer(text):
            start = _expand_left(text, m.start())
            if start is not None:
                spans.append((start, m.end(), 0.85))

        for m in _STREET_LINE.finditer(text):
            spans.append((m.start(), m.end(), 0.6))

        for start, end, score in _merge(spans):
            value = text[start:end].strip(" \t\r\n,;")
            if len(value) < 12:
                continue
            offset = text.index(value, start, end) if value in text[start:end] else start
            yield Entity(
                type=PIIType.ADDRESS,
                start=offset,
                end=offset + len(value),
                text=value,
                recognizer=self.name,
                score=score,
            )


def _expand_left(text: str, tail_start: int) -> int | None:
    """Find where the address block containing ``tail_start`` begins."""
    window_start = max(0, tail_start - _MAX_LEFT)
    window = text[window_start:tail_start]

    # Nearest hard boundary before the tail.
    cut = 0
    for m in _BOUNDARY.finditer(window):
        cut = m.end()
    candidate = window_start + cut

    # If a label like "Registered Office:" sits right there, start after it.
    label = _LABEL_RE.match(text, candidate)
    if label:
        candidate = label.end()

    # Skip leading whitespace/punctuation.
    while candidate < tail_start and text[candidate] in " \t\r\n,;-–":
        candidate += 1
    if candidate >= tail_start:
        return None
    return candidate


def _merge(spans: list[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """Merge overlapping address spans, keeping the widest and best-scored."""
    if not spans:
        return []
    spans.sort()
    merged = [list(spans[0])]
    for start, end, score in spans[1:]:
        last = merged[-1]
        if start <= last[1]:
            last[1] = max(last[1], end)
            last[2] = max(last[2], score)
        else:
            merged.append([start, end, score])
    return [(int(a), int(b), float(c)) for a, b, c in merged]
