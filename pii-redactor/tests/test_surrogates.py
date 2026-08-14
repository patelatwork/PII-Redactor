"""Surrogate generation: consistent, deterministic, and safe by construction."""

from __future__ import annotations

import re

import pytest

from piiredact import RedactionConfig, Redactor
from piiredact.recognizers.patterns import luhn_ok, verhoeff_ok
from piiredact.surrogates import SurrogateFactory
from piiredact.types import PIIType

RULES_ONLY = RedactionConfig(spacy_model=None)


def redact(text: str, config: RedactionConfig = RULES_ONLY) -> str:
    return Redactor(config).redact(text).text


# --------------------------------------------------------------------------
# the assignment's core requirement
# --------------------------------------------------------------------------


def test_same_person_gets_the_same_fake_name_everywhere():
    text = (
        "Mr. Rashi Prakash Patil is our Chairman. "
        "Rashi Prakash Patil signed the agreement. "
        "We contacted Rashi Prakash Patil again."
    )
    out = redact(text)
    names = set(re.findall(r"Mr\. (\w+ \w+ \w+)|(?:^|\. )(\w+ \w+ \w+) s?igned", out))
    assert "Rashi" not in out
    # exactly one distinct surrogate, repeated three times
    surrogate = out.split("Mr. ")[1].split(" is our")[0]
    assert out.count(surrogate) == 3
    assert names is not None


def test_short_form_agrees_with_full_name():
    """'Rashi Patil' must become the two-part form of 'Rashi Prakash Patil'."""
    factory = SurrogateFactory(RULES_ONLY)
    factory.prime(Redactor(RULES_ONLY).analyze("Mr. Rashi Prakash Patil, Managing Director."))
    full = factory.surrogate(PIIType.PERSON, "Rashi Prakash Patil").split()
    short = factory.surrogate(PIIType.PERSON, "Rashi Patil").split()
    assert len(full) == 3 and len(short) == 2
    assert short == [full[0], full[2]]


def test_email_local_part_follows_the_person():
    out = redact("Mr. Rashi Prakash Patil signed. Write to rashi.patil@acme.co.in.")
    person = out.split("Mr. ")[1].split(" signed")[0].lower().split()
    local = out.split("@")[0].rsplit(" ", 1)[-1]
    assert local == f"{person[0]}.{person[2]}"


def test_uppercase_mentions_stay_uppercase():
    out = redact("OUR PROMOTERS: RASHI PRAKASH PATIL AND OTHERS")
    assert re.search(r"OUR PROMOTERS: [A-Z]+ [A-Z]+ [A-Z]+", out)


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


def test_same_seed_gives_identical_output():
    text = "Mr. Rashi Patil, rashi@acme.co.in, +91 20 45053237"
    assert redact(text) == redact(text)


def test_different_seed_gives_different_output():
    text = "Mr. Rashi Patil signed"
    a = redact(text, RULES_ONLY.with_(seed="seed-a"))
    b = redact(text, RULES_ONLY.with_(seed="seed-b"))
    assert a != b


# --------------------------------------------------------------------------
# format preservation and safety
# --------------------------------------------------------------------------


def test_phone_keeps_country_code_and_punctuation():
    out = redact("Telephone: + 91 20 4505 3237")
    assert re.search(r"\+ 91 \d\d \d{4} \d{4}", out)
    assert "4505 3237" not in out


def test_generated_card_is_luhn_valid_and_same_brand():
    factory = SurrogateFactory(RULES_ONLY)
    fake = factory.surrogate(PIIType.CREDIT_CARD, "4111 1111 1111 1111")
    digits = re.sub(r"\D", "", fake)
    assert fake.count(" ") == 3
    assert digits[0] == "4" and len(digits) == 16 and luhn_ok(digits)
    assert digits != "4111111111111111"


def test_generated_aadhaar_has_a_valid_check_digit():
    factory = SurrogateFactory(RULES_ONLY)
    fake = factory.surrogate(PIIType.AADHAAR, "2345 6789 0124")
    assert verhoeff_ok(re.sub(r"\D", "", fake))


def test_generated_ssn_uses_a_never_issued_range():
    """Area numbers 900-999 have never been assigned, so we cannot mint a real one."""
    factory = SurrogateFactory(RULES_ONLY)
    fake = factory.surrogate(PIIType.SSN, "214-77-3910")
    assert fake.startswith("9") and re.fullmatch(r"9\d\d-\d\d-\d{4}", fake)


def test_generated_ip_is_in_a_documentation_range():
    factory = SurrogateFactory(RULES_ONLY)
    v4 = factory.surrogate(PIIType.IP_ADDRESS, "8.8.8.8")
    assert v4.rsplit(".", 1)[0] in {"192.0.2", "198.51.100", "203.0.113"}
    v6 = factory.surrogate(PIIType.IP_ADDRESS, "2001:4860:4860::8888")
    assert v6.startswith("2001:0db8:")


def test_generated_email_domain_is_reserved():
    factory = SurrogateFactory(RULES_ONLY)
    assert factory.surrogate(PIIType.EMAIL, "a.b@acme.co.in").endswith(".example.com")


def test_role_mailbox_local_part_is_preserved_but_domain_is_not():
    out = redact("Email: cs.connect@kshinternational.com")
    assert "cs.connect@" in out
    assert "kshinternational" not in out


def test_dob_keeps_its_format_but_changes_the_date():
    out = redact("His date of birth is 12/04/1958.")
    assert re.search(r"date of birth is \d\d/\d\d/\d{4}", out)
    assert "12/04/1958" not in out


def test_company_keeps_its_legal_suffix():
    out = redact("Acme Wire Industries Limited was incorporated in 1979.")
    assert out.endswith("was incorporated in 1979.")
    assert "Limited" in out and "Acme" not in out


# --------------------------------------------------------------------------
# mapping
# --------------------------------------------------------------------------


def test_mapping_records_every_replacement():
    redactor = Redactor(RULES_ONLY)
    redactor.redact("Mr. Rashi Patil, rashi@acme.co.in, +91 20 45053237")
    rows = redactor.mapping_rows()
    assert {row["type"] for row in rows} == {"PERSON", "EMAIL", "PHONE"}
    assert all(row["original"] and row["surrogate"] for row in rows)


@pytest.mark.parametrize("pii_type", list(PIIType))
def test_every_type_has_a_surrogate_handler(pii_type):
    """Guard against adding a PIIType and forgetting the generator."""
    value = SurrogateFactory(RULES_ONLY).surrogate(pii_type, "Sample Value 1234")
    assert value and "REDACTED" not in value
