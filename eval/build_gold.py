"""Resolve hand-written labels into an offset-annotated gold set.

The hand-authored files (``gold/*_labels.json``) contain only *strings* -- the
text of each PII instance.  This script pairs them with the actual document text
and computes character offsets, which keeps the labelling honest: a label that
does not occur verbatim in the source is a hard error, not a silent miss.

    python eval/build_gold.py            # writes eval/gold/*_gold.json

Prospectus records are addressed by ``index`` (position in the document's
paragraph stream) *and* ``anchor`` (the opening text of that paragraph).  The
index selects, the anchor verifies -- necessary because the filing repeats
whole paragraphs verbatim ("Maharashtra, India", the ICICI Securities block),
so text alone cannot identify one, and an index alone would silently point
somewhere else if the extractor ever changed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from piiredact.documents.docx_io import (  # noqa: E402
    iter_paragraphs,
    load,
    paragraph_text,
)

HERE = Path(__file__).resolve().parent
GOLD_DIR = HERE / "gold"
DEFAULT_DOCX = HERE.parent / "data" / "input" / "Red Herring Prospectus.docx"


def _flexible(needle: str) -> re.Pattern[str]:
    """Match ``needle`` allowing any run of whitespace where it has whitespace."""
    parts = [re.escape(tok) for tok in needle.split()]
    return re.compile(r"\s+".join(parts))


def locate(haystack: str, needle: str, occurrence: int = 1) -> tuple[int, int]:
    matches = list(_flexible(needle).finditer(haystack))
    if len(matches) < occurrence:
        raise ValueError(
            f"label {needle!r} (occurrence {occurrence}) not found in:\n{haystack[:400]}"
        )
    match = matches[occurrence - 1]
    return match.start(), match.end()


def resolve_record(text: str, labels: list[dict], record_id: str) -> list[dict]:
    out = []
    for label in labels:
        start, end = locate(text, label["text"], label.get("occurrence", 1))
        out.append(
            {
                "type": label["type"],
                "start": start,
                "end": end,
                "text": text[start:end],
            }
        )
    overlapping = _find_overlap(out)
    if overlapping:
        raise ValueError(f"{record_id}: gold labels overlap: {overlapping}")
    return sorted(out, key=lambda e: e["start"])


def _find_overlap(entities: list[dict]) -> tuple[dict, dict] | None:
    ordered = sorted(entities, key=lambda e: e["start"])
    for left, right in zip(ordered, ordered[1:], strict=False):
        if right["start"] < left["end"]:
            return left, right
    return None


def document_paragraphs(docx_path: Path) -> list[str]:
    return [paragraph_text(p) for p in iter_paragraphs(load(docx_path))]


def build_from_document(docx_path: Path, labels_file: str) -> dict:
    labels = json.loads((GOLD_DIR / labels_file).read_text(encoding="utf-8"))
    paragraphs = document_paragraphs(docx_path)

    records = []
    for spec in labels["records"]:
        index, anchor = spec["index"], spec["anchor"]
        if not 0 <= index < len(paragraphs):
            raise ValueError(f"paragraph index {index} is out of range")
        text = paragraphs[index]
        if not text.startswith(anchor):
            raise ValueError(
                f"paragraph {index} starts {text[:60]!r}, expected anchor {anchor[:60]!r}"
            )
        records.append(
            {
                "id": f"p{index}",
                "index": index,
                "stratum": spec["stratum"],
                "text": text,
                "entities": resolve_record(text, spec["entities"], f"p{index}"),
            }
        )
    return {"source": docx_path.name, "records": records}


def build_synthetic() -> dict:
    labels = json.loads((GOLD_DIR / "synthetic_labels.json").read_text(encoding="utf-8"))
    records = [
        {
            "id": spec["id"],
            "stratum": spec["stratum"],
            "text": spec["text"],
            "entities": resolve_record(spec["text"], spec["entities"], spec["id"]),
        }
        for spec in labels["records"]
    ]
    return {"source": "synthetic", "records": records}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", type=Path, default=DEFAULT_DOCX)
    args = parser.parse_args(argv)

    for name, data in (
        ("prospectus_gold.json", build_from_document(args.docx, "prospectus_labels.json")),
        ("holdout_gold.json", build_from_document(args.docx, "holdout_labels.json")),
        ("synthetic_gold.json", build_synthetic()),
    ):
        path = GOLD_DIR / name
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        total = sum(len(r["entities"]) for r in data["records"])
        empty = sum(1 for r in data["records"] if not r["entities"])
        print(
            f"{path.name}: {len(data['records'])} records "
            f"({empty} with no PII), {total} labelled entities"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
