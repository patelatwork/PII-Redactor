"""Core domain types shared by every layer of the pipeline.

Everything downstream (recognizers, resolution, surrogates, document IO) speaks
in terms of :class:`Entity` spans over a plain-text string.  Keeping the model
this small is what makes adding a new PII type cheap -- see README, "Adding a
new PII type".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PIIType(str, Enum):
    """The categories of personal data the tool knows how to handle.

    Values are stable strings because they are written to the audit CSV, the
    mapping file and the HTTP API, and are used as gold-label names in
    ``eval/gold``.
    """

    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    ORGANIZATION = "ORGANIZATION"
    ADDRESS = "ADDRESS"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    SSN = "SSN"
    CREDIT_CARD = "CREDIT_CARD"
    IP_ADDRESS = "IP_ADDRESS"
    URL = "URL"
    # India-specific identifiers -- the source corpus is an Indian IPO filing.
    PAN = "PAN"
    AADHAAR = "AADHAAR"
    DIN = "DIN"
    PASSPORT = "PASSPORT"


#: Higher wins when two detections overlap.  Deterministic identifiers beat
#: model/rule guesses, and long structured spans (ADDRESS) beat the tokens they
#: happen to contain (PERSON, ORGANIZATION).
TYPE_PRIORITY: dict[PIIType, int] = {
    PIIType.EMAIL: 100,
    PIIType.URL: 95,
    PIIType.CREDIT_CARD: 92,
    PIIType.SSN: 91,
    PIIType.AADHAAR: 90,
    PIIType.PAN: 89,
    PIIType.PASSPORT: 88,
    PIIType.DIN: 87,
    PIIType.IP_ADDRESS: 86,
    PIIType.PHONE: 85,
    PIIType.DATE_OF_BIRTH: 80,
    PIIType.ADDRESS: 60,
    PIIType.ORGANIZATION: 40,
    PIIType.PERSON: 30,
}


@dataclass(frozen=True, slots=True)
class Entity:
    """A detected span of personal data in a text.

    ``start``/``end`` are Python string offsets into the text that was analysed
    (half-open, i.e. ``text[start:end] == text_``).
    """

    type: PIIType
    start: int
    end: int
    text: str
    recognizer: str
    score: float = 1.0
    #: Free-form provenance, e.g. the label that anchored a contextual match.
    notes: str = ""

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"empty or inverted span: {self.start}..{self.end}")

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def priority(self) -> int:
        return TYPE_PRIORITY.get(self.type, 0)

    def overlaps(self, other: Entity) -> bool:
        return self.start < other.end and other.start < self.end

    def as_dict(self) -> dict:
        return {
            "type": self.type.value,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "recognizer": self.recognizer,
            "score": round(self.score, 3),
            "notes": self.notes,
        }


@dataclass(slots=True)
class RedactionResult:
    """Output of redacting one text chunk."""

    text: str
    entities: list[Entity] = field(default_factory=list)
    #: ``(type, original) -> surrogate`` actually applied.
    mapping: dict[tuple[str, str], str] = field(default_factory=dict)
