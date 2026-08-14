"""Document format adapters.

Every adapter reduces a file to a list of text segments, hands them to the
shared analysis path in :mod:`piiredact.documents.segments`, and writes the
result back.  Adding a format (HTML, EML, CSV) means adding one module here --
the detection and surrogate layers are untouched.
"""

from __future__ import annotations

from pathlib import Path

from ..redactor import Redactor
from ..types import Entity
from . import docx_io, pdf_io
from .segments import SegmentedText, analyze_segments

__all__ = [
    "SegmentedText",
    "analyze_segments",
    "docx_io",
    "pdf_io",
    "redact_document",
    "SUPPORTED_SUFFIXES",
]

SUPPORTED_SUFFIXES = (".docx", ".pdf", ".txt", ".md")


def redact_document(
    source: str | Path,
    destination: str | Path,
    redactor: Redactor,
) -> list[Entity]:
    """Redact any supported input into a .docx (or .txt) output.

    ``.docx`` in → ``.docx`` out with formatting preserved.
    ``.pdf``/``.txt`` in → the text is converted to a .docx first, then redacted
    through the same code path, so there is exactly one implementation to test.
    """
    source, destination = Path(source), Path(destination)
    suffix = source.suffix.lower()

    if suffix == ".docx":
        return docx_io.redact_docx(source, destination, redactor)

    if suffix == ".pdf":
        blocks = pdf_io.extract_blocks(source)
    elif suffix in (".txt", ".md"):
        blocks = [
            b.strip()
            for b in source.read_text(encoding="utf-8", errors="replace").split("\n\n")
            if b.strip()
        ]
    else:
        raise ValueError(
            f"unsupported input {suffix!r}; expected one of {SUPPORTED_SUFFIXES}"
        )

    if destination.suffix.lower() == ".txt":
        entities, buckets = analyze_segments(redactor, blocks)
        redacted = [redactor.apply(b, found) for b, found in zip(blocks, buckets, strict=True)]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n\n".join(redacted), encoding="utf-8")
        return entities

    # Materialise the extracted text as a .docx, then run the .docx path so the
    # in-place, run-preserving rewriter is the only substitution implementation.
    intermediate = destination.with_suffix(".source.docx")
    pdf_io.write_blocks(blocks, intermediate)
    try:
        return docx_io.redact_docx(intermediate, destination, redactor)
    finally:
        intermediate.unlink(missing_ok=True)
