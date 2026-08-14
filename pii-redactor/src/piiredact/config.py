"""Runtime configuration for the redaction pipeline.

Everything a deployment might want to tune lives here so that the API, the CLI
and the tests all share one source of truth.  Values can come from a YAML file
(``--config``) or from environment variables (``PIIREDACT_*``), which is what
the container image uses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .types import PIIType

#: Public bodies, exchanges, regulators and market infrastructure.  These are
#: organisations, but they do not identify a *private* party, and redacting them
#: destroys the meaning of a regulatory filing ("SEBI", "BSE Limited").  Treated
#: as non-PII by default; set ``redact_public_institutions=True`` to include
#: them.  See README, "Precision choices we made deliberately".
PUBLIC_INSTITUTION_ALLOWLIST: frozenset[str] = frozenset(
    s.lower()
    for s in [
        "Securities and Exchange Board of India",
        "SEBI",
        "Reserve Bank of India",
        "RBI",
        "BSE Limited",
        "BSE",
        "National Stock Exchange of India Limited",
        "NSE",
        "Stock Exchanges",
        "Designated Stock Exchange",
        "National Securities Depository Limited",
        "NSDL",
        "Central Depository Services (India) Limited",
        "CDSL",
        "Registrar of Companies",
        "RoC",
        "Ministry of Corporate Affairs",
        "Government of India",
        "Income Tax Department",
        "Institute of Chartered Accountants of India",
        "ICAI",
        "Supreme Court of India",
        "High Court",
        "Companies Act",
        "Insurance Regulatory and Development Authority of India",
        "IRDAI",
        "Foreign Exchange Management Act",
        "International Monetary Fund",
        "World Bank",
        "United Nations",
    ]
)

#: Domains belonging to the public bodies above.  Redacting ``www.sebi.gov.in``
#: is not a privacy gain and makes a regulatory filing unreadable.
PUBLIC_DOMAIN_ALLOWLIST: frozenset[str] = frozenset(
    [
        "sebi.gov.in", "bseindia.com", "nseindia.com", "rbi.org.in",
        "mca.gov.in", "nsdl.co.in", "nsdl.com", "cdslindia.com",
        "incometax.gov.in", "epfindia.gov.in", "india.gov.in", "fbil.org.in",
    ]
)

#: Any host under these suffixes is a government/public site.
PUBLIC_DOMAIN_SUFFIXES: tuple[str, ...] = (".gov.in", ".nic.in", ".gov", ".int")

#: Local-parts (or fragments of them) that name a *function*, not a person.
#: Role mailboxes are kept intact -- they identify a desk, and preserving them
#: keeps the redacted filing usable.
ROLE_MAILBOX_STEMS: frozenset[str] = frozenset(
    [
        "ipo", "cs", "hr", "info", "admin", "sales", "support", "care",
        "help", "contact", "office", "mail", "grievance", "investor",
        "customer", "service", "connect", "pro", "rm", "ops", "cmg",
        "secretary", "compliance", "enquiry", "query", "desk", "team",
        "group", "corp", "finance", "legal", "accounts", "billing",
        "noreply", "no-reply", "webmaster", "postmaster", "complaints",
    ]
)

DEFAULT_ENABLED_TYPES: tuple[PIIType, ...] = tuple(PIIType)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class RedactionConfig:
    """Tunables for a single redaction run."""

    #: Which PII types to detect and replace.
    enabled_types: tuple[PIIType, ...] = DEFAULT_ENABLED_TYPES

    #: Seed for surrogate generation.  The same seed + same input always yields
    #: the same fake values, which is what makes runs reproducible and makes the
    #: redacted document internally consistent ("Rashi Patil" is *always*
    #: "John Doe").  Rotate it per customer/tenant in production.
    seed: str = "piiredact-v1"

    #: spaCy model used by the NER layer.  Set to ``None`` (or leave the model
    #: uninstalled) to run rules-only; the pipeline degrades gracefully.
    spacy_model: str | None = "en_core_web_sm"

    #: Accept spaCy's ORG label as an organisation.  Off by default: measured on
    #: this corpus it produced ~4x more false positives than true ones
    #: ("ASBA Bidder", "Basis of Allotment", "Anchor Investors"), while the
    #: legal-suffix rule plus propagation already covers real company names.
    #: See EVALUATION.md, "Why NER organisations are disabled".
    ner_organizations: bool = False

    #: Minimum score for a detection to be applied.
    min_score: float = 0.4

    #: Second pass: once a PERSON/ORGANIZATION is confidently identified
    #: anywhere in the document, redact every other mention of it too.
    enable_propagation: bool = True

    #: Treat regulators/exchanges/public bodies as organisations to redact.
    redact_public_institutions: bool = False

    #: Write the reversible ``original -> surrogate`` map.  This file is itself
    #: sensitive; disable it for one-way anonymisation.
    emit_mapping: bool = True

    #: Extra literals to always redact / never redact, by type.
    extra_deny: dict[PIIType, tuple[str, ...]] = field(default_factory=dict)
    extra_allow: frozenset[str] = frozenset()

    def with_(self, **kwargs: Any) -> RedactionConfig:
        return replace(self, **kwargs)

    def is_enabled(self, pii_type: PIIType) -> bool:
        return pii_type in self.enabled_types

    def is_allowlisted(self, value: str) -> bool:
        """True when ``value`` names a public body rather than a private party.

        Adjacent duplicate tokens are collapsed first because PDF/table
        extraction routinely doubles a heading into its cell ("BSE BSE Limited").
        """
        tokens = value.lower().split()
        deduped: list[str] = []
        for token in tokens:
            if not deduped or deduped[-1] != token:
                deduped.append(token)
        key = " ".join(deduped)
        if key in self.extra_allow or " ".join(tokens) in self.extra_allow:
            return True
        if self.redact_public_institutions:
            return False
        if key in PUBLIC_INSTITUTION_ALLOWLIST:
            return True
        # Also match the name without its legal suffix ("BSE Limited" -> "BSE").
        while deduped and deduped[-1].strip(".,") in {
            "limited", "ltd", "llp", "inc", "corporation", "plc"
        }:
            deduped.pop()
            if " ".join(deduped) in PUBLIC_INSTITUTION_ALLOWLIST:
                return True
        return False

    def is_public_domain(self, host: str) -> bool:
        """True for regulator / government hostnames."""
        if self.redact_public_institutions:
            return False
        host = host.strip().lower().removeprefix("www.")
        if host in PUBLIC_DOMAIN_ALLOWLIST:
            return True
        return host.endswith(PUBLIC_DOMAIN_SUFFIXES)

    # -- constructors ----------------------------------------------------

    @classmethod
    def from_env(cls) -> RedactionConfig:
        cfg = cls()
        types_raw = os.getenv("PIIREDACT_TYPES")
        if types_raw:
            cfg.enabled_types = tuple(
                PIIType(t.strip().upper()) for t in types_raw.split(",") if t.strip()
            )
        cfg.seed = os.getenv("PIIREDACT_SEED", cfg.seed)
        model = os.getenv("PIIREDACT_SPACY_MODEL")
        if model is not None:
            cfg.spacy_model = model or None
        cfg.min_score = float(os.getenv("PIIREDACT_MIN_SCORE", cfg.min_score))
        cfg.enable_propagation = _env_bool("PIIREDACT_PROPAGATION", cfg.enable_propagation)
        cfg.ner_organizations = _env_bool("PIIREDACT_NER_ORGS", cfg.ner_organizations)
        cfg.redact_public_institutions = _env_bool(
            "PIIREDACT_REDACT_PUBLIC_INSTITUTIONS", cfg.redact_public_institutions
        )
        cfg.emit_mapping = _env_bool("PIIREDACT_EMIT_MAPPING", cfg.emit_mapping)
        return cfg

    @classmethod
    def from_yaml(cls, path: str | Path) -> RedactionConfig:
        import yaml  # imported lazily: only needed when --config is used

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        cfg = cls()
        if "enabled_types" in data:
            cfg.enabled_types = tuple(PIIType(t.upper()) for t in data["enabled_types"])
        for key in (
            "seed",
            "spacy_model",
            "min_score",
            "enable_propagation",
            "ner_organizations",
            "redact_public_institutions",
            "emit_mapping",
        ):
            if key in data:
                setattr(cfg, key, data[key])
        if "extra_allow" in data:
            cfg.extra_allow = frozenset(s.lower() for s in data["extra_allow"])
        if "extra_deny" in data:
            cfg.extra_deny = {
                PIIType(k.upper()): tuple(v) for k, v in data["extra_deny"].items()
            }
        return cfg
