"""Deterministic, pattern-based recognizers.

These carry the highest confidence in the pipeline because each one is either
structurally unambiguous (email, IP) or backed by a checksum / context anchor
(credit card via Luhn, Aadhaar via Verhoeff, DIN and passport via a label).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..config import RedactionConfig
from ..lexicons import DOB_CONTEXT, NON_DOB_DATE_CONTEXT
from ..types import Entity, PIIType
from .base import RegexRecognizer

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def luhn_ok(digits: str) -> bool:
    """Standard Luhn/mod-10 checksum used by all major card schemes."""
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6), (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4), (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2), (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_ok(digits: str) -> bool:
    """Checksum used by Aadhaar numbers."""
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][ord(ch) - 48]]
    return c == 0


def context_window(text: str, start: int, before: int = 140, after: int = 40) -> str:
    return text[max(0, start - before) : start + after].lower()


# --------------------------------------------------------------------------
# recognizers
# --------------------------------------------------------------------------


class EmailRecognizer(RegexRecognizer):
    name = "email"
    pii_type = PIIType.EMAIL
    types = (PIIType.EMAIL,)
    score = 0.99
    pattern = re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,24}\b"
    )


class UrlRecognizer(RegexRecognizer):
    """Websites.  These identify the *organisation*, so we treat them the same
    way we treat company names -- see README for why this is a deliberate call.
    """

    name = "url"
    pii_type = PIIType.URL
    types = (PIIType.URL,)
    score = 0.9
    pattern = re.compile(
        r"\b(?:https?://|www\.)[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*"
        r"\.(?:com|net|org|in|co\.in|io|gov|edu|biz|info)\b(?:/[^\s\"'<>]*)?",
        re.IGNORECASE,
    )

    def validate(self, value: str, match: re.Match[str], text: str) -> bool:
        # An email's domain part can look like a bare URL; the email recognizer
        # has priority, but skipping obvious cases keeps the audit log tidy.
        return not (match.start() > 0 and text[match.start() - 1] == "@")

    def skip(self, value: str, config: RedactionConfig) -> bool:
        host = re.sub(r"^https?://", "", value).split("/")[0]
        return config.is_public_domain(host)


class PhoneRecognizer:
    """Phone numbers.

    Two shapes are accepted, both requiring evidence beyond "a run of digits":

    1. an explicit international prefix (``+91 20 4505 3237``), or
    2. a label such as ``Telephone:``/``Mobile``/``Fax`` in front of the number.

    A bare 10-digit run is *not* matched.  In a financial filing that would
    swallow share counts and rupee amounts, and precision matters more here than
    catching an unlabelled number.  See README, "Known false negatives".
    """

    name = "phone"
    pii_type = PIIType.PHONE
    types = (PIIType.PHONE,)

    _international = re.compile(r"\+\s?\d{1,3}(?:[\s\-.]?\s?\d{2,10}){1,5}(?!\d)")
    _labelled = re.compile(
        r"(?P<label>Tel(?:ephone|\.)?|Phone|Mobile|Mob\.?|Fax|Contact\s+(?:No|Number)|"
        r"Helpline|Landline)\s*(?:No\.?|Number)?\s*[:\-–]?\s*"
        r"(?P<num>\+?\d[\d\s\-().]{7,20}\d)",
        re.IGNORECASE,
    )
    _us = re.compile(r"\(?\b\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b")

    def analyze(self, text: str, config: RedactionConfig) -> Iterable[Entity]:
        if not config.is_enabled(PIIType.PHONE):
            return
        seen: set[tuple[int, int]] = set()
        for pattern, group, score in (
            (self._international, 0, 0.95),
            (self._labelled, "num", 0.9),
            (self._us, 0, 0.75),
        ):
            for m in pattern.finditer(text):
                start, end = m.span(group)
                raw = _trim_wrapped_tail(text[start:end].rstrip(" \t\r\n.,;:)"))
                end = start + len(raw)
                digits = re.sub(r"\D", "", raw)
                if not (8 <= len(digits) <= 15):
                    continue
                if (start, end) in seen:
                    continue
                seen.add((start, end))
                yield Entity(
                    type=PIIType.PHONE,
                    start=start,
                    end=end,
                    text=raw,
                    recognizer=self.name,
                    score=score,
                )


#: Labels that mean a dashed 3-2-4 number is a business reference, not an SSN.
#: The assignment calls this out explicitly: order and ticket numbers must not
#: be redacted, and "400-25-1000" on an invoice line is structurally identical
#: to a valid SSN.
NON_SSN_CONTEXT: tuple[str, ...] = (
    "invoice", "order", "ticket", "reference", "ref.", "ref ", "po ", "p.o.",
    "purchase", "policy", "case", "claim", "docket", "sr. no", "s. no",
    "part no", "sku", "batch", "lot ", "account no", "receipt",
)


#: A phone number may legitimately wrap across lines in a table cell
#: ("+91 \n22 \n40094400"), so newlines are valid separators.  But that lets the
#: pattern run past the end of the field into whatever the next line starts with.
#: A wrapped number always breaks *after* a complete group, so a final group of
#: one to three digits sitting alone on a new line is the next field, not part of
#: the number.
_WRAPPED_TAIL = re.compile(r"\n[^\S\n]*\d{1,3}$")


def _trim_wrapped_tail(raw: str) -> str:
    return _WRAPPED_TAIL.sub("", raw).rstrip()


class SSNRecognizer(RegexRecognizer):
    """US Social Security Numbers, with the SSA's structural invalid ranges
    excluded (area 000/666/9xx, group 00, serial 0000)."""

    name = "ssn"
    pii_type = PIIType.SSN
    types = (PIIType.SSN,)
    score = 0.95
    pattern = re.compile(
        r"(?:(?<=\bSSN[:\s])|(?<=\bSSN\s)|(?<![\d\-]))"
        r"(?!000|666|9\d\d)\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}(?![\d\-])"
    )

    def validate(self, value: str, match: re.Match[str], text: str) -> bool:
        ctx = context_window(text, match.start(), before=60, after=0)
        if "ssn" in ctx or "social security" in ctx:
            return True
        return not any(cue in ctx for cue in NON_SSN_CONTEXT)


class SSNLabelledRecognizer(RegexRecognizer):
    """Unformatted 9-digit SSN, only when an explicit label precedes it."""

    name = "ssn_labelled"
    pii_type = PIIType.SSN
    types = (PIIType.SSN,)
    score = 0.9
    group = "num"
    pattern = re.compile(
        r"(?:SSN|Social\s+Security(?:\s+Number)?)\s*[:#\-]?\s*"
        r"(?P<num>(?!000|666|9\d\d)\d{3}[-\s]?(?!00)\d{2}[-\s]?(?!0000)\d{4})\b",
        re.IGNORECASE,
    )


class CreditCardRecognizer(RegexRecognizer):
    """Card numbers for the major schemes, confirmed with the Luhn checksum.

    The checksum is what makes this safe to run over a document full of
    financial figures: an arbitrary 16-digit amount passes Luhn only ~10% of the
    time, and the brand prefixes cut that further.
    """

    name = "credit_card"
    pii_type = PIIType.CREDIT_CARD
    types = (PIIType.CREDIT_CARD,)
    score = 0.95
    pattern = re.compile(
        r"(?<![\d.,])(?:"
        r"3[47]\d{2}[\s\-]?\d{6}[\s\-]?\d{5}|"  # amex: 4-6-5 grouping
        r"(?:"
        r"4\d{3}|5[1-5]\d{2}|2(?:2[2-9]\d|[3-6]\d{2}|7[01]\d|720)|"  # visa / mc
        r"6(?:011|5\d{2}|4[4-9]\d)|3[47]\d{2}|3(?:0[0-5]|[68]\d)\d|35\d{2}"
        r")[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,7}"
        r")(?!\d)(?![.,]\d)"
    )

    def validate(self, value: str, match: re.Match[str], text: str) -> bool:
        digits = re.sub(r"\D", "", value)
        return 13 <= len(digits) <= 19 and luhn_ok(digits)


class IPAddressRecognizer(RegexRecognizer):
    name = "ip_address"
    pii_type = PIIType.IP_ADDRESS
    types = (PIIType.IP_ADDRESS,)
    score = 0.95
    pattern = re.compile(
        r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)(?!\d)(?!\.\d)"
        r"|(?<![:\w])(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}(?![:\w])"
    )

    #: Cues that a dotted quad is a version, build or clause number.
    _VERSION_CONTEXT = (
        "version", "build", "release", "firmware", "schema", "revision",
        "clause", "section", "regulation", "paragraph", "rule ",
    )
    _NETWORK_CONTEXT = ("ip ", "ip address", "ipv4", "server", "host", "gateway",
                        "logged in from", "connected", "session", "subnet")

    def validate(self, value: str, match: re.Match[str], text: str) -> bool:
        if ":" in value:
            return True
        # A version cue only disqualifies the quad it directly introduces, so it
        # is checked in a tight window; network cues may sit further away.
        if any(
            cue in context_window(text, match.start(), before=24, after=0)
            for cue in self._VERSION_CONTEXT
        ):
            return False
        ctx = context_window(text, match.start(), before=60, after=25)
        if any(cue in ctx for cue in self._NETWORK_CONTEXT):
            return True
        # Otherwise require the shape to be distinctly address-like: either a
        # well-known private/loopback/documentation range, or at least two octets
        # above 31 (version numbers rarely have two large components).
        octets = [int(p) for p in value.split(".")]
        if octets[0] in (10, 127) or (octets[0] == 192 and octets[1] == 168):
            return True
        if octets[0] == 172 and 16 <= octets[1] <= 31:
            return True
        return sum(o > 31 for o in octets) >= 2


class PANRecognizer(RegexRecognizer):
    """Indian Permanent Account Number: 5 letters, 4 digits, 1 letter, where the
    4th letter encodes holder type."""

    name = "pan"
    pii_type = PIIType.PAN
    types = (PIIType.PAN,)
    score = 0.92
    pattern = re.compile(r"\b[A-Z]{3}[ABCFGHLJPTK][A-Z]\d{4}[A-Z]\b")


class AadhaarRecognizer(RegexRecognizer):
    """Indian Aadhaar number: 12 digits, never starting with 0 or 1, validated
    with the Verhoeff checksum so financial figures do not slip through."""

    name = "aadhaar"
    pii_type = PIIType.AADHAAR
    types = (PIIType.AADHAAR,)
    score = 0.93
    pattern = re.compile(r"(?<![\d.,])[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}(?!\d)(?![.,]\d)")

    def validate(self, value: str, match: re.Match[str], text: str) -> bool:
        digits = re.sub(r"\D", "", value)
        if len(digits) != 12:
            return False
        if verhoeff_ok(digits):
            return True
        ctx = context_window(text, match.start(), before=60, after=20)
        return "aadhaar" in ctx or "aadhar" in ctx or "uid" in ctx


class DINRecognizer(RegexRecognizer):
    """Director Identification Number -- an 8-digit government ID tied to a
    named individual.  Only matched next to an explicit ``DIN`` label, because
    bare 8-digit numbers are everywhere in a prospectus."""

    name = "din"
    pii_type = PIIType.DIN
    types = (PIIType.DIN,)
    score = 0.9
    group = "num"
    pattern = re.compile(
        r"(?:DIN|Director\s+Identification\s+Number)\s*[:\-–]?\s*(?P<num>\d{6,8})\b",
        re.IGNORECASE,
    )


class DINContextRecognizer(RegexRecognizer):
    """DIN identified by its position in a board-of-directors table.

    The ``DIN`` column header sits in a different extraction block from the
    values, so the label-anchored rule above never sees it.  What *is* reliably
    adjacent is the director's designation: in every such table the row reads
    ``<name> | <designation> | <DIN> | <address>``.  A 6-8 digit number directly
    after a designation is therefore a DIN.

    The digits may be split by a line break -- PDF extraction wraps the column,
    turning ``00135070`` into ``0013507\\n0`` -- so the pattern tolerates one.
    """

    name = "din_designation"
    pii_type = PIIType.DIN
    types = (PIIType.DIN,)
    score = 0.75
    group = "num"
    pattern = re.compile(
        r"(?:Chairman|Managing\s+Director|Joint\s+Managing\s+Director|"
        r"Whole[\s\-]?time\s+Director|Executive\s+Director|Independent\s+Director|"
        r"Non[\s\-]?Executive\s+Director|Nominee\s+Director|Additional\s+Director|"
        r"Director)\s*\n?\s*(?P<num>\d{6,8}(?:\s*\n\s*\d{1,2})?)(?!\d)",
        re.IGNORECASE,
    )


class PassportRecognizer(RegexRecognizer):
    name = "passport"
    pii_type = PIIType.PASSPORT
    types = (PIIType.PASSPORT,)
    score = 0.88
    group = "num"
    pattern = re.compile(
        r"(?:Passport)\s*(?:No\.?|Number)?\s*[:\-–]?\s*(?P<num>[A-PR-WYa-pr-wy]\s?\d{7})\b",
        re.IGNORECASE,
    )


class DateOfBirthRecognizer:
    """Dates, but only those anchored to birth context.

    A prospectus is saturated with dates (agreement dates, fiscal year ends, bid
    dates).  Redacting all of them would gut the document and tank precision, so
    a date is only PII when a ``date of birth`` / ``born on`` style cue sits
    within a short window before it.
    """

    name = "date_of_birth"
    pii_type = PIIType.DATE_OF_BIRTH
    types = (PIIType.DATE_OF_BIRTH,)

    _months = (
        "January|February|March|April|May|June|July|August|September|October|"
        "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
    )
    _date = re.compile(
        rf"\b(?:"
        rf"\d{{1,2}}[/\-.]\d{{1,2}}[/\-.]\d{{2,4}}"
        rf"|\d{{4}}[/\-.]\d{{1,2}}[/\-.]\d{{1,2}}"
        rf"|(?:{_months})\s+\d{{1,2}},?\s+\d{{4}}"
        rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_months}),?\s+\d{{4}}"
        rf")\b",
        re.IGNORECASE,
    )

    def analyze(self, text: str, config: RedactionConfig) -> Iterable[Entity]:
        if not config.is_enabled(PIIType.DATE_OF_BIRTH):
            return
        lowered = text.lower()
        for m in self._date.finditer(text):
            ctx = lowered[max(0, m.start() - 90) : m.start()]
            if not any(cue in ctx for cue in DOB_CONTEXT):
                continue
            # A DOB cue can still be followed by an unrelated date in a long
            # window; reject when a business-date cue is closer to the match.
            tail = lowered[max(0, m.start() - 30) : m.start()]
            if any(cue in tail for cue in NON_DOB_DATE_CONTEXT):
                continue
            yield Entity(
                type=PIIType.DATE_OF_BIRTH,
                start=m.start(),
                end=m.end(),
                text=m.group(0),
                recognizer=self.name,
                score=0.9,
                notes="dob-context",
            )


#: Instantiated in priority-ish order; the resolver sorts properly anyway.
PATTERN_RECOGNIZERS = (
    EmailRecognizer(),
    UrlRecognizer(),
    PhoneRecognizer(),
    CreditCardRecognizer(),
    SSNRecognizer(),
    SSNLabelledRecognizer(),
    AadhaarRecognizer(),
    PANRecognizer(),
    DINRecognizer(),
    DINContextRecognizer(),
    PassportRecognizer(),
    IPAddressRecognizer(),
    DateOfBirthRecognizer(),
)
