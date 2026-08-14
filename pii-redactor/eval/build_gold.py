"""Resolve hand-written labels into an offset-annotated gold set.

The hand-authored files (``gold/*_labels.json``) contain only *strings* -- the
text of each PII instance.  This script pairs them with the actual document text
and computes character offsets, which keeps the labelling honest: a label that
does not occur verbatim in the source is a hard error, not a silent miss.

    python eval/build_gold.py            # writes eval/gold/*_gold.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from piiredact.documents import pdf_io  # noqa: E402

HERE = Path(__file__).resolve().parent
GOLD_DIR = HERE / "gold"
DEFAULT_PDF = HERE.parent / "data" / "input" / "Red Herring Prospectus.docx.pdf"


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


def build_prospectus(pdf_path: Path) -> dict:
    labels = json.loads((GOLD_DIR / "prospectus_labels.json").read_text(encoding="utf-8"))
    blocks = pdf_io.extract_blocks(pdf_path)

    records = []
    for spec in labels["records"]:
        # Exact prefix match: anchors are copied verbatim from the extractor's
        # output, and a flexible match would conflate the several near-identical
        # intermediary blocks this filing repeats.
        anchor = spec["anchor"]
        found = [b for b in blocks if b.startswith(anchor)]
        if len(found) != 1:
            raise ValueError(
                f"anchor {anchor!r} matched {len(found)} blocks; it must match exactly one"
            )
        text = found[0]
        records.append(
            {
                "id": anchor[:48],
                "stratum": spec["stratum"],
                "text": text,
                "entities": resolve_record(text, spec["entities"], anchor[:48]),
            }
        )
    return {"source": str(pdf_path.name), "records": records}


def build_synthetic() -> dict:
    labels = json.loads((GOLD_DIR / "synthetic_labels.json").read_text(encoding="utf-8"))
    records = []
    for spec in labels["records"]:
        records.append(
            {
                "id": spec["id"],
                "stratum": spec["stratum"],
                "text": spec["text"],
                "entities": resolve_record(spec["text"], spec["entities"], spec["id"]),
            }
        )
    return {"source": "synthetic", "records": records}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    args = parser.parse_args(argv)

    for name, data in (
        ("prospectus_gold.json", build_prospectus(args.pdf)),
        ("synthetic_gold.json", build_synthetic()),
    ):
        path = GOLD_DIR / name
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        total = sum(len(r["entities"]) for r in data["records"])
        print(f"{path.name}: {len(data['records'])} records, {total} labelled entities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
