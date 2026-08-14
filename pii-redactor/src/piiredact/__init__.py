"""piiredact -- detect and pseudonymise personal data in documents."""

from __future__ import annotations

from .analyzer import Analyzer
from .config import RedactionConfig
from .redactor import Redactor
from .surrogates import SurrogateFactory
from .types import Entity, PIIType, RedactionResult

__version__ = "1.0.0"

__all__ = [
    "Analyzer",
    "Entity",
    "PIIType",
    "RedactionConfig",
    "RedactionResult",
    "Redactor",
    "SurrogateFactory",
    "__version__",
]
