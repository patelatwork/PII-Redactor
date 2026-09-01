"""Fake-value generation.

The assignment asks for *pseudonymisation*, not blackout: every real value is
replaced by a realistic fake one.  Three properties drive the design.

**Consistent.** The same person is the same fake person everywhere in the
document, and short forms agree with long forms -- if "Kushal Subbayya Hegde"
becomes "Arjun Ramesh Iyer", then "Kushal Hegde" becomes "Arjun Iyer" and
``kushal.hegde@…`` becomes ``arjun.iyer@…``.

**Deterministic.** Surrogates are derived from ``HMAC(seed, type|value)``, so a
re-run produces byte-identical output and two shards of a corpus processed on
different machines agree with each other.  Rotating ``seed`` re-randomises
everything.

**Safe by construction.** Generated values are drawn from reserved ranges
wherever one exists -- ``example.com`` domains (RFC 2606), ``192.0.2.0/24`` and
friends (RFC 5737), ``2001:db8::/32`` (RFC 3849), and SSNs in the never-issued
900 area -- so redaction can never accidentally mint a real person's identifier.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import date, timedelta

from .config import ROLE_MAILBOX_STEMS, RedactionConfig
from .lexicons import ORG_SUFFIXES
from .recognizers.patterns import luhn_ok
from .recognizers.propagation import PersonIdentityIndex, normalise


class OrganizationIdentityIndex:
    """Maps shorter org mentions back to the canonical confirmed company name."""

    def __init__(self, canonicals: list[str] | tuple[str, ...] | set[str]) -> None:
        self._canonicals: list[tuple[frozenset[str], str]] = []
        for org in sorted({normalise(c) for c in canonicals}, key=len, reverse=True):
            keys = frozenset(t.lower() for t in org.split())
            if any(keys <= existing for existing, _ in self._canonicals):
                continue
            self._canonicals.append((keys, org))

    def resolve(self, mention: str) -> str:
        mention = normalise(mention)
        tokens = frozenset(t.lower() for t in mention.split())
        matches = [name for keys, name in self._canonicals if tokens <= keys]
        if len(matches) == 1:
            return matches[0]
        return mention
from .types import Entity, PIIType

# --------------------------------------------------------------------------
# name pools
# --------------------------------------------------------------------------

FIRST_NAMES = (
    "Arjun", "Neha", "Vikram", "Priya", "Rohan", "Ananya", "Karan", "Meera",
    "Aditya", "Sneha", "Nikhil", "Divya", "Sameer", "Ishita", "Varun", "Kavya",
    "Rahul", "Tara", "Manish", "Riya", "John", "Sarah", "Peter", "Emily",
    "Daniel", "Laura", "Thomas", "Grace", "Oliver", "Maya",
)
MIDDLE_NAMES = (
    "Ramesh", "Kumar", "Anand", "Prakash", "Suresh", "Mohan", "Nath", "Raj",
    "Dev", "Chandra", "Lee", "Marie", "James", "Anne", "Paul", "Rose",
)
LAST_NAMES = (
    "Iyer", "Menon", "Kulkarni", "Bhatt", "Sharma", "Rao", "Nair", "Deshmukh",
    "Chawla", "Gokhale", "Malhotra", "Sinha", "Pillai", "Chatterjee", "Verma",
    "Doe", "Parker", "Whitfield", "Harper", "Sullivan", "Beaumont", "Ashcroft",
)

COMPANY_HEADS = (
    "Vertex", "Northwind", "Blueharbour", "Silverpine", "Ironbridge",
    "Crestline", "Amberfield", "Redstone", "Lighthouse", "Meridian",
    "Stonegate", "Cobalt", "Larkspur", "Sunhaven", "Fairmount", "Everline",
)
COMPANY_TAILS = (
    "Dynamics", "Industries", "Technologies", "Enterprises", "Solutions",
    "Systems", "Traders", "Manufacturing", "Holdings", "Ventures", "Works",
    "Components", "Materials", "Logistics", "Advisors", "Consultants",
)

STREET_NAMES = (
    "Maple", "Cedar", "Juniper", "Willow", "Aspen", "Linden", "Birch",
    "Hawthorn", "Sycamore", "Alder", "Rosewood", "Palm",
)
LOCALITIES = (
    "Greenfield", "Westbrook", "Ashvale", "Norbury", "Elmgrove", "Highmoor",
    "Kingsmead", "Riverton", "Oakridge", "Fairlane",
)
CITIES = (
    "Rampur", "Nayanagar", "Vidyapur", "Anandgram", "Suryapeth", "Mohanpur",
    "Chandrapuri", "Devikot", "Springfield", "Riverdale",
)
STATES = (
    "Northshire", "Westmark", "Eastvale", "Southbury", "Midland", "Highvale",
)

_SUFFIX_TOKENS = frozenset(t.lower().strip(".") for s in ORG_SUFFIXES for t in s.split())


# --------------------------------------------------------------------------
# deterministic randomness
# --------------------------------------------------------------------------


class _Stream:
    """A reproducible byte stream keyed by (seed, type, value)."""

    def __init__(self, seed: str, key: str) -> None:
        self._seed = seed.encode()
        self._key = key.encode()
        self._counter = 0
        self._buf = b""

    def _refill(self) -> None:
        msg = self._key + b"|" + str(self._counter).encode()
        self._buf += hmac.new(self._seed, msg, hashlib.sha256).digest()
        self._counter += 1

    def bits(self, n_bytes: int = 4) -> int:
        while len(self._buf) < n_bytes:
            self._refill()
        chunk, self._buf = self._buf[:n_bytes], self._buf[n_bytes:]
        return int.from_bytes(chunk, "big")

    def below(self, n: int) -> int:
        return self.bits() % max(1, n)

    def pick(self, pool):
        return pool[self.below(len(pool))]

    def digits(self, n: int) -> str:
        return "".join(str(self.below(10)) for _ in range(n))


# --------------------------------------------------------------------------
# factory
# --------------------------------------------------------------------------


class SurrogateFactory:
    """Produces (and remembers) the fake value for every detected entity."""

    def __init__(self, config: RedactionConfig | None = None) -> None:
        self.config = config or RedactionConfig()
        self._cache: dict[tuple[str, str], str] = {}
        self._person_index = PersonIdentityIndex([])
        self._organization_index = OrganizationIdentityIndex([])
        self._person_personas: dict[str, list[str]] = {}

    # -- identity priming ------------------------------------------------

    def prime(self, entities: list[Entity]) -> None:
        """Register the canonical identities before any substitution.

        Called once per document so that short mentions and e-mail local parts
        can be rendered from the same persona as the full name, and shortened
        organisation names map to the same canonical company identity.
        """
        person_canonicals = {
            normalise(e.text).title() if e.text.isupper() else normalise(e.text)
            for e in entities
            if e.type is PIIType.PERSON
        }
        self._person_index = PersonIdentityIndex(person_canonicals)

        org_canonicals = {
            normalise(e.text)
            for e in entities
            if e.type is PIIType.ORGANIZATION
        }
        self._organization_index = OrganizationIdentityIndex(org_canonicals)

    # -- public API ------------------------------------------------------

    def for_entity(self, entity: Entity) -> str:
        return self.surrogate(entity.type, entity.text)

    def surrogate(self, pii_type: PIIType, value: str) -> str:
        key = (pii_type.value, normalise(value))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._generate(pii_type, value)
        self._cache[key] = result
        return result

    @property
    def mapping(self) -> dict[tuple[str, str], str]:
        return dict(self._cache)

    # -- generation ------------------------------------------------------

    def _stream(self, pii_type: PIIType, value: str) -> _Stream:
        return _Stream(self.config.seed, f"{pii_type.value}|{normalise(value).lower()}")

    def _generate(self, pii_type: PIIType, value: str) -> str:
        handler = _HANDLERS.get(pii_type)
        if handler is None:  # pragma: no cover - every type has a handler
            return f"[REDACTED-{pii_type.value}]"
        return handler(self, value)

    # -- per-type handlers ----------------------------------------------

    def _person(self, value: str) -> str:
        mention = normalise(value)
        canonical = self._person_index.resolve(mention)
        persona = self._persona(canonical)
        positions = self._person_index.token_positions(canonical, mention)
        tokens = [persona[i % len(persona)] for i in positions] or persona[:2]
        out = " ".join(tokens)
        return _match_case(value, out)

    def _persona(self, canonical: str) -> list[str]:
        cached = self._person_personas.get(canonical.lower())
        if cached is not None:
            return cached
        stream = self._stream(PIIType.PERSON, canonical)
        n = max(2, len(canonical.split()))
        persona = [stream.pick(FIRST_NAMES)]
        for _ in range(n - 2):
            persona.append(stream.pick(MIDDLE_NAMES))
        persona.append(stream.pick(LAST_NAMES))
        self._person_personas[canonical.lower()] = persona
        return persona

    def _organization(self, value: str) -> str:
        mention = normalise(value)
        canonical = self._organization_index.resolve(mention)
        if canonical != mention and canonical.lower() != mention.lower():
            return self._organization(canonical)

        tokens = canonical.split()
        suffix: list[str] = []
        while tokens and tokens[-1].lower().strip(".,") in _SUFFIX_TOKENS:
            suffix.insert(0, tokens.pop())
        core_key = " ".join(tokens).lower() or canonical.lower()
        stream = self._stream(PIIType.ORGANIZATION, core_key)
        core = f"{stream.pick(COMPANY_HEADS)} {stream.pick(COMPANY_TAILS)}"
        out = " ".join([core, *suffix]) if suffix else core
        return _match_case(value, out)

    def _company_slug(self, value: str) -> str:
        """Stable lowercase slug used for fake e-mail domains and websites."""
        stream = self._stream(PIIType.ORGANIZATION, normalise(value).lower())
        return f"{stream.pick(COMPANY_HEADS)}{stream.pick(COMPANY_TAILS)}".lower()

    def _email(self, value: str) -> str:
        value = value.strip()
        local, _, domain = value.partition("@")
        return f"{self._email_local(local)}@{self._email_domain(domain)}"

    def _email_local(self, local: str) -> str:
        """Person-shaped locals follow that person's fake name; role addresses
        (``ipo@``, ``customercare@``, ``cs.connect@``) are kept -- they identify
        a function, not a human, and preserving them keeps the document usable.

        Trailing digits are stripped before the test and restored afterwards, so
        ``pravin.teli2`` is recognised as a person rather than skipped.
        """
        parts = re.split(r"([._\-])", local)
        if len(parts) > 9:  # more than 5 fragments: not a name, leave it alone
            return local.lower()
        fragments = parts[::2]
        separators = parts[1::2]

        cores = [re.sub(r"\d+$", "", f) for f in fragments]
        identifying = [
            i
            for i, core in enumerate(cores)
            if len(core) >= 3 and core.isalpha() and not _is_role_word(core)
        ]
        if not identifying:
            # Pure role mailbox: cs.connect@, customercare@, ipo@.
            return local.lower()

        # The whole local part is a person's name: "eric.bacha", "anand.soni".
        if len(identifying) == len(fragments) >= 2:
            fake = self._person(" ".join(c.capitalize() for c in cores)).split()
            rebuilt = [fake[i % len(fake)].lower() for i in range(len(fragments))]
            return "".join(
                frag + (separators[i] if i < len(separators) else "")
                for i, frag in enumerate(rebuilt)
            )

        # Otherwise replace only the identifying fragments and keep the role
        # words, so "kshinternational.ipo" becomes "<fake-company>.ipo".
        rebuilt = list(fragments)
        for i in identifying:
            rebuilt[i] = self._company_slug(cores[i])
        return "".join(
            frag.lower() + (separators[i] if i < len(separators) else "")
            for i, frag in enumerate(rebuilt)
        )

    def _email_domain(self, domain: str) -> str:
        domain = domain.strip().lower()
        if not domain:
            return "example.com"
        if self.config.is_public_domain(domain):
            return domain
        # RFC 2606 reserves example.com/net/org for documentation, so a
        # generated address can never reach a real mailbox.
        return f"{self._company_slug(domain)}.example.com"

    def _url(self, value: str) -> str:
        m = re.match(r"(?P<scheme>https?://)?(?P<host>[^/]+)(?P<path>/.*)?$", value.strip())
        if not m:
            return "www.example.com"
        host = m.group("host")
        if self.config.is_public_domain(host):
            return value
        prefix = "www." if host.lower().startswith("www.") else ""
        slug = self._company_slug(host)
        return f"{m.group('scheme') or ''}{prefix}{slug}.example.com{m.group('path') or ''}"

    def _phone(self, value: str) -> str:
        """Keep the country code and the exact punctuation; replace the rest.

        Matches the assignment's own example (``+91 9876543210`` →
        ``+91 1234567645``): the dialling prefix is not personal, the subscriber
        number is.
        """
        stream = self._stream(PIIType.PHONE, value)
        out: list[str] = []
        seen_cc = not value.lstrip().startswith("+")
        cc_digits = 0
        for ch in value:
            if not ch.isdigit():
                out.append(ch)
                continue
            if not seen_cc and cc_digits < 2:
                out.append(ch)
                cc_digits += 1
                if cc_digits == 2:
                    seen_cc = True
                continue
            out.append(str(stream.below(9) + 1) if not out or not out[-1].isdigit()
                       else str(stream.below(10)))
        return "".join(out)

    def _address(self, value: str) -> str:
        stream = self._stream(PIIType.ADDRESS, value)
        if re.search(r"\bIndia\b", value, re.IGNORECASE) or re.search(r"\d{3}\s?\d{3}\b", value):
            pin = f"{stream.below(9) + 1}{stream.digits(2)} {stream.digits(3)}"
            return (
                f"{stream.below(400) + 1}, {stream.pick(LOCALITIES)} Residency, "
                f"{stream.pick(STREET_NAMES)} Road, {stream.pick(CITIES)} – {pin} "
                f"{stream.pick(STATES)}, India"
            )
        return (
            f"{stream.below(9000) + 100} {stream.pick(STREET_NAMES)} Street, "
            f"{stream.pick(CITIES)}, {stream.pick(STATES)} {stream.below(90000) + 10000}"
        )

    def _date_of_birth(self, value: str) -> str:
        """Shift the date by a stable pseudo-random offset, keeping the format.

        Shifting rather than randomising preserves rough age ordering, which
        analysts often still need, while breaking the exact identifier.
        """
        stream = self._stream(PIIType.DATE_OF_BIRTH, value)
        offset = timedelta(days=stream.below(2400) - 1200)
        parsed = _parse_date(value)
        if parsed is None:
            return f"{stream.below(28) + 1}/{stream.below(12) + 1}/{stream.below(40) + 1960}"
        shifted = parsed[0] + offset
        return _render_date(shifted, parsed[1])

    def _ssn(self, value: str) -> str:
        # Area numbers 900-999 have never been issued by the SSA.
        stream = self._stream(PIIType.SSN, value)
        fake = f"9{stream.digits(2)}-{stream.digits(2)}-{stream.digits(4)}"
        return fake if "-" in value else fake.replace("-", " " if " " in value else "")

    def _credit_card(self, value: str) -> str:
        """Same brand prefix and length, new Luhn-valid body, same punctuation."""
        stream = self._stream(PIIType.CREDIT_CARD, value)
        digits = re.sub(r"\D", "", value)
        body = digits[0] + stream.digits(len(digits) - 2)
        for check in range(10):
            candidate = body + str(check)
            if luhn_ok(candidate):
                break
        return _apply_digit_mask(value, candidate)

    def _ip_address(self, value: str) -> str:
        stream = self._stream(PIIType.IP_ADDRESS, value)
        if ":" in value:
            # RFC 3849 documentation prefix.
            groups = ":".join(f"{stream.bits(2) & 0xFFFF:04x}" for _ in range(4))
            return f"2001:0db8:{groups}"
        # RFC 5737 documentation ranges.
        block = stream.pick(("192.0.2", "198.51.100", "203.0.113"))
        return f"{block}.{stream.below(254) + 1}"

    def _pan(self, value: str) -> str:
        stream = self._stream(PIIType.PAN, value)
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        head = "".join(stream.pick(letters) for _ in range(3))
        return f"{head}P{stream.pick(letters)}{stream.digits(4)}{stream.pick(letters)}"

    def _aadhaar(self, value: str) -> str:
        stream = self._stream(PIIType.AADHAAR, value)
        body = str(stream.below(8) + 2) + stream.digits(10)
        fake = body + _verhoeff_check(body)
        return _apply_digit_mask(value, fake)

    def _din(self, value: str) -> str:
        stream = self._stream(PIIType.DIN, value)
        return _apply_digit_mask(value, stream.digits(len(re.sub(r"\D", "", value))))

    def _passport(self, value: str) -> str:
        stream = self._stream(PIIType.PASSPORT, value)
        return _apply_digit_mask(
            value, stream.digits(7), letter=stream.pick("ABCDEFGHJKLMNPQRSTUVW")
        )


_HANDLERS = {
    PIIType.PERSON: SurrogateFactory._person,
    PIIType.ORGANIZATION: SurrogateFactory._organization,
    PIIType.EMAIL: SurrogateFactory._email,
    PIIType.URL: SurrogateFactory._url,
    PIIType.PHONE: SurrogateFactory._phone,
    PIIType.ADDRESS: SurrogateFactory._address,
    PIIType.DATE_OF_BIRTH: SurrogateFactory._date_of_birth,
    PIIType.SSN: SurrogateFactory._ssn,
    PIIType.CREDIT_CARD: SurrogateFactory._credit_card,
    PIIType.IP_ADDRESS: SurrogateFactory._ip_address,
    PIIType.PAN: SurrogateFactory._pan,
    PIIType.AADHAAR: SurrogateFactory._aadhaar,
    PIIType.DIN: SurrogateFactory._din,
    PIIType.PASSPORT: SurrogateFactory._passport,
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)


def _match_case(original: str, replacement: str) -> str:
    """Mirror the original's capitalisation style onto the surrogate."""
    letters = [c for c in original if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return replacement.upper()
    return replacement


def _is_role_word(word: str) -> bool:
    """True when an e-mail local-part fragment names a desk, not a person.

    Substring matching catches the run-together forms filings actually use --
    ``customerservice``, ``ipocmg``, ``rm6.ifbpune``.
    """
    word = word.lower()
    if word in ROLE_MAILBOX_STEMS:
        return True
    return any(stem in word for stem in ROLE_MAILBOX_STEMS if len(stem) >= 4)


def _apply_digit_mask(original: str, digits: str, letter: str | None = None) -> str:
    """Rewrite ``original`` keeping every non-digit character in place."""
    out, i = [], 0
    used_letter = False
    for ch in original:
        if ch.isdigit():
            out.append(digits[i] if i < len(digits) else "0")
            i += 1
        elif ch.isalpha() and letter is not None and not used_letter:
            out.append(letter)
            used_letter = True
        else:
            out.append(ch)
    return "".join(out)


def _parse_date(value: str) -> tuple[date, str] | None:
    value = value.strip()
    m = re.fullmatch(r"(\d{1,2})([/\-.])(\d{1,2})\2(\d{2,4})", value)
    if m:
        d, sep, mth, y = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4))
        y += 1900 if y < 100 and y > 30 else (2000 if y < 100 else 0)
        try:
            return date(y, mth, d), f"dmy{sep}"
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})([/\-.])(\d{1,2})\2(\d{1,2})", value)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(3)), int(m.group(4))), f"ymd{m.group(2)}"
        except ValueError:
            return None
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", value)
    if m:
        month = _month_index(m.group(1))
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2))), "MDY"
            except ValueError:
                return None
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(\d{4})", value)
    if m:
        month = _month_index(m.group(2))
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1))), "DMY"
            except ValueError:
                return None
    return None


def _month_index(name: str) -> int | None:
    name = name.lower()
    for i, month in enumerate(_MONTHS, start=1):
        if month.lower().startswith(name) or name.startswith(month.lower()[:3]):
            return i
    return None


def _render_date(value: date, fmt: str) -> str:
    if fmt.startswith("dmy"):
        sep = fmt[3]
        return f"{value.day:02d}{sep}{value.month:02d}{sep}{value.year}"
    if fmt.startswith("ymd"):
        sep = fmt[3]
        return f"{value.year}{sep}{value.month:02d}{sep}{value.day:02d}"
    if fmt == "MDY":
        return f"{_MONTHS[value.month - 1]} {value.day}, {value.year}"
    return f"{value.day} {_MONTHS[value.month - 1]}, {value.year}"


def _verhoeff_check(digits: str) -> str:
    from .recognizers.patterns import _VERHOEFF_D, _VERHOEFF_P

    inv = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][ord(ch) - 48]]
    return str(inv[c])
