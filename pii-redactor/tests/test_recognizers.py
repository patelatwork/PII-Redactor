"""Detector behaviour: what must be caught, and what must be left alone.

The negative cases matter as much as the positive ones -- the assignment grades
precision explicitly, and a filing is full of numbers that resemble identifiers.
"""

from __future__ import annotations

import pytest

from piiredact import Analyzer, RedactionConfig
from piiredact.recognizers.patterns import luhn_ok, verhoeff_ok
from piiredact.types import PIIType

RULES_ONLY = RedactionConfig(spacy_model=None)


def detect(text: str, config: RedactionConfig = RULES_ONLY) -> set[tuple[str, str]]:
    return {(e.type.value, " ".join(e.text.split())) for e in Analyzer(config).analyze(text)}


def types_in(text: str, config: RedactionConfig = RULES_ONLY) -> set[str]:
    return {t for t, _ in detect(text, config)}


# --------------------------------------------------------------------------
# checksums
# --------------------------------------------------------------------------


@pytest.mark.parametrize("digits", ["4111111111111111", "5555555555554444",
                                    "378282246310005", "6011111111111117"])
def test_luhn_accepts_known_test_cards(digits):
    assert luhn_ok(digits)


def test_luhn_rejects_altered_card():
    assert not luhn_ok("4111111111111112")


def test_verhoeff_round_trip():
    assert verhoeff_ok("234567890124")
    assert not verhoeff_ok("234567890123")


# --------------------------------------------------------------------------
# positive detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("write to john.doe@example.co.in please", ("EMAIL", "john.doe@example.co.in")),
        ("Telephone: + 91 20 45053237", ("PHONE", "+ 91 20 45053237")),
        ("call +1 415-555-0182 now", ("PHONE", "+1 415-555-0182")),
        ("SSN 214-77-3910 on file", ("SSN", "214-77-3910")),
        ("card 4111 1111 1111 1111 charged", ("CREDIT_CARD", "4111 1111 1111 1111")),
        ("amex 3782 822463 10005 charged", ("CREDIT_CARD", "3782 822463 10005")),
        ("connected from 203.0.113.47 today", ("IP_ADDRESS", "203.0.113.47")),
        ("date of birth 12/04/1958.", ("DATE_OF_BIRTH", "12/04/1958")),
        ("PAN: ABCPK7890L", ("PAN", "ABCPK7890L")),
        ("Aadhaar 2345 6789 0124", ("AADHAAR", "2345 6789 0124")),
        ("DIN: 00135070", ("DIN", "00135070")),
        ("Passport No: M1234567", ("PASSPORT", "M1234567")),
        ("see www.acme-wire.com for details", ("URL", "www.acme-wire.com")),
        ("Mr. Rashi Prakash Patil signed", ("PERSON", "Rashi Prakash Patil")),
        ("Acme Wire Industries Limited filed", ("ORGANIZATION", "Acme Wire Industries Limited")),
    ],
)
def test_detects(text, expected):
    assert expected in detect(text)


def test_ip_at_end_of_sentence_is_still_found():
    """Regression: a trailing full stop used to be read as a fifth octet."""
    assert ("IP_ADDRESS", "198.51.100.9") in detect("connected via 198.51.100.9.")


def test_all_caps_legal_suffix_is_matched():
    """Regression: 'FAMILY TRUST' in an all-caps promoter list was missed."""
    found = detect("OUR PROMOTERS: RASHI PATIL, DHAULAGIRI FAMILY TRUST AND OTHERS")
    assert ("ORGANIZATION", "DHAULAGIRI FAMILY TRUST") in found
    assert ("PERSON", "RASHI PATIL") in found


def test_din_from_director_designation():
    """The DIN column header is in another block; the designation anchors it."""
    text = "Rohan Dey \nManaging Director \n0011419\n3 \n12 Elm Road"
    assert "DIN" in types_in(text)


# --------------------------------------------------------------------------
# precision: things that must NOT be redacted
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,forbidden",
    [
        ("Order 1234 5678 9012 3456 was cancelled", "CREDIT_CARD"),
        ("invoice reference 400-25-1000 attached", "SSN"),
        ("ticket 555-12-3456789 closed", "SSN"),
        ("schema version 3.47.2.1 deployed", "IP_ADDRESS"),
        ("build 6.011.111.111 shipped", "IP_ADDRESS"),
        ("the agreement dated December 10, 2025", "DATE_OF_BIRTH"),
        ("Bid/Offer opens on Tuesday, December 16, 2025", "DATE_OF_BIRTH"),
        ("total payable 5,55,555.00 this quarter", "CREDIT_CARD"),
        ("quote your PAN and Aadhaar when contacting us", "PAN"),
        ("9876543210 equity shares were allotted", "PHONE"),
    ],
)
def test_does_not_detect(text, forbidden):
    assert forbidden not in types_in(text)


def test_public_institutions_are_not_organizations():
    found = detect("approved by BSE Limited and the Securities and Exchange Board of India")
    assert not any(t == "ORGANIZATION" for t, _ in found)


def test_public_institutions_redacted_when_configured():
    config = RULES_ONLY.with_(redact_public_institutions=True)
    assert "ORGANIZATION" in types_in("approved by BSE Limited", config)


def test_government_urls_are_left_alone():
    assert "URL" not in types_in("see www.sebi.gov.in for the circular")


def test_jargon_is_not_a_person():
    """spaCy and loose rules both like to call filing jargon a person."""
    text = (
        "The Anchor Investor Application Form, the Basis of Allotment and the "
        "Bidder's DP ID must be provided. Non-GAAP Measures are defined below."
    )
    assert "PERSON" not in types_in(text)


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------


def test_disabled_types_are_not_detected():
    config = RULES_ONLY.with_(enabled_types=(PIIType.EMAIL,))
    assert types_in("a@b.com and +91 20 45053237", config) == {"EMAIL"}


def test_entities_never_overlap():
    text = (
        "Contact Person: Rashi Patil\n"
        "Acme Wire Industries Limited, 12, Tower 2, Baner, Pune – 411 045 Maharashtra, India\n"
        "Email rashi.patil@acme.co.in Telephone: + 91 20 45053237"
    )
    entities = Analyzer(RULES_ONLY).analyze(text)
    for left, right in zip(entities, entities[1:], strict=False):
        assert left.end <= right.start, f"{left} overlaps {right}"


def test_address_does_not_swallow_the_phone_number():
    """Regression: the address span used to expand left across contact fields."""
    text = (
        "Email: cs@acme.co.in Telephone: + 91 20 45053237\n"
        "12, Tower 2, Baner, Pune 411 045 Maharashtra, India"
    )
    found = detect(text)
    assert ("PHONE", "+ 91 20 45053237") in found
    assert any(t == "ADDRESS" and "Telephone" not in v for t, v in found)
