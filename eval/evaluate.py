"""Score the redactor against the gold sets and emit an evaluation report.

    python eval/build_gold.py        # resolve hand labels -> offsets
    python eval/evaluate.py          # score, print, and write eval/results.json

Three views of the same run, because one number hides too much:

**Strict entity matching** -- a prediction counts only if its type *and* its
exact character span equal a gold entity's.  This is the harshest view and the
one that punishes a slightly-too-wide address span.

**Partial entity matching** -- same type and any character overlap, matched
one-to-one, best overlap first.  This is the operationally meaningful view: if
the tool replaced "201, Tower-2, Montreal Business Centre … India" where gold
said the address started one word later, the personal data still got redacted.

**Token-level accuracy** -- every whitespace token in the sample is classified
redacted / not-redacted by both gold and system.  Unlike the entity views this
one has true negatives, so a real *accuracy* figure can be computed over a
well-defined denominator: the tokens of the document.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_gold import document_paragraphs  # noqa: E402

from piiredact import RedactionConfig, Redactor  # noqa: E402
from piiredact.types import Entity  # noqa: E402

HERE = Path(__file__).resolve().parent
GOLD_DIR = HERE / "gold"
TOKEN_RE = re.compile(r"\S+")


# --------------------------------------------------------------------------
# counting
# --------------------------------------------------------------------------


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    fp_examples: list[str] = field(default_factory=list)
    fn_examples: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else float("nan")

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else float("nan")

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p == p and r == r and p + r else float("nan")

    @property
    def accuracy(self) -> float:
        """Jaccard accuracy when there are no true negatives, plain accuracy when
        there are (token level)."""
        denominator = self.tp + self.fp + self.fn + self.tn
        return (self.tp + self.tn) / denominator if denominator else float("nan")

    def as_dict(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
            "precision": _round(self.precision),
            "recall": _round(self.recall),
            "f1": _round(self.f1),
            "accuracy": _round(self.accuracy),
        }


def _round(value: float) -> float | None:
    return None if value != value else round(value, 4)


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------


def match_strict(gold: list[dict], pred: list[Entity]) -> list[tuple[dict | None, Entity | None]]:
    remaining = list(pred)
    pairs: list[tuple[dict | None, Entity | None]] = []
    for g in gold:
        hit = next(
            (
                p
                for p in remaining
                if p.type.value == g["type"] and p.start == g["start"] and p.end == g["end"]
            ),
            None,
        )
        if hit is not None:
            remaining.remove(hit)
        pairs.append((g, hit))
    pairs.extend((None, p) for p in remaining)
    return pairs


def match_partial(gold: list[dict], pred: list[Entity]) -> list[tuple[dict | None, Entity | None]]:
    """One-to-one, greedy by descending overlap."""
    candidates = [
        (min(g["end"], p.end) - max(g["start"], p.start), gi, pi)
        for gi, g in enumerate(gold)
        for pi, p in enumerate(pred)
        if p.type.value == g["type"] and p.start < g["end"] and g["start"] < p.end
    ]
    candidates.sort(reverse=True)

    used_gold: set[int] = set()
    used_pred: set[int] = set()
    pairs: list[tuple[dict | None, Entity | None]] = []
    for _, gi, pi in candidates:
        if gi in used_gold or pi in used_pred:
            continue
        used_gold.add(gi)
        used_pred.add(pi)
        pairs.append((gold[gi], pred[pi]))
    pairs.extend((g, None) for gi, g in enumerate(gold) if gi not in used_gold)
    pairs.extend((None, p) for pi, p in enumerate(pred) if pi not in used_pred)
    return pairs


def tally(pairs: list[tuple[dict | None, Entity | None]], per_type: dict[str, Counts]) -> None:
    for g, p in pairs:
        if g is not None and p is not None:
            per_type[g["type"]].tp += 1
        elif g is not None:
            counts = per_type[g["type"]]
            counts.fn += 1
            if len(counts.fn_examples) < 8:
                counts.fn_examples.append(" ".join(g["text"].split())[:70])
        elif p is not None:
            counts = per_type[p.type.value]
            counts.fp += 1
            if len(counts.fp_examples) < 8:
                counts.fp_examples.append(" ".join(p.text.split())[:70])


# --------------------------------------------------------------------------
# token-level view
# --------------------------------------------------------------------------


def token_counts(text: str, gold: list[dict], pred: list[Entity]) -> Counts:
    gold_mask = _mask(len(text), [(g["start"], g["end"]) for g in gold])
    pred_mask = _mask(len(text), [(p.start, p.end) for p in pred])

    counts = Counts()
    for token in TOKEN_RE.finditer(text):
        lo, hi = token.span()
        is_gold = any(gold_mask[lo:hi])
        is_pred = any(pred_mask[lo:hi])
        if is_gold and is_pred:
            counts.tp += 1
        elif is_gold:
            counts.fn += 1
        elif is_pred:
            counts.fp += 1
        else:
            counts.tn += 1
    return counts


def _mask(length: int, spans: list[tuple[int, int]]) -> list[bool]:
    mask = [False] * length
    for start, end in spans:
        for i in range(max(0, start), min(length, end)):
            mask[i] = True
    return mask


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def predict(records: list[dict], redactor: Redactor, context: Path | None) -> list[list[Entity]]:
    """Run the analyzer and return predictions per gold record.

    When ``context`` is a document, the analyzer sees the *whole* document and
    the gold blocks are sliced back out afterwards.  That is how the tool runs in
    production, and it matters: the propagation pass earns most of its recall
    from mentions elsewhere in the file, so scoring the sample in isolation would
    understate the deployed system.
    """
    from piiredact.documents.segments import SegmentedText

    if context is None:
        segmented = SegmentedText([r["text"] for r in records])
        return segmented.bucket(redactor.analyze(segmented.joined))

    paragraphs = document_paragraphs(context)
    segmented = SegmentedText(paragraphs)
    buckets = segmented.bucket(redactor.analyze(segmented.joined))

    out = []
    for record in records:
        index = record["index"]
        if paragraphs[index] != record["text"]:
            raise ValueError(
                f"gold record {record['id']!r} no longer matches paragraph {index}; "
                "re-run eval/build_gold.py"
            )
        out.append(buckets[index])
    return out


def evaluate(gold_file: Path, redactor: Redactor, context: Path | None = None) -> dict:
    data = json.loads(gold_file.read_text(encoding="utf-8"))
    records = data["records"]
    predictions = predict(records, redactor, context)

    strict: dict[str, Counts] = defaultdict(Counts)
    partial: dict[str, Counts] = defaultdict(Counts)
    tokens = Counts()

    for record, pred in zip(records, predictions, strict=True):
        gold = record["entities"]
        tally(match_strict(gold, pred), strict)
        tally(match_partial(gold, pred), partial)
        record_tokens = token_counts(record["text"], gold, pred)
        for attr in ("tp", "fp", "fn", "tn"):
            setattr(tokens, attr, getattr(tokens, attr) + getattr(record_tokens, attr))

    return {
        "gold_file": gold_file.name,
        "context": context.name if context else "sample only",
        "records": len(records),
        "gold_entities": sum(len(r["entities"]) for r in records),
        "predicted_entities": sum(len(p) for p in predictions),
        "strict": {t: c.as_dict() for t, c in sorted(strict.items())},
        "partial": {t: c.as_dict() for t, c in sorted(partial.items())},
        "partial_errors": {
            t: {"false_positives": c.fp_examples, "false_negatives": c.fn_examples}
            for t, c in sorted(partial.items())
            if c.fp_examples or c.fn_examples
        },
        "token_level": tokens.as_dict(),
        "micro_strict": _micro(strict),
        "micro_partial": _micro(partial),
    }


def _micro(per_type: dict[str, Counts]) -> dict:
    total = Counts()
    for counts in per_type.values():
        total.tp += counts.tp
        total.fp += counts.fp
        total.fn += counts.fn
    return total.as_dict()


def print_report(result: dict) -> None:
    print(f"\n=== {result['gold_file']}  (context: {result['context']}) ===")
    print(
        f"{result['records']} records | {result['gold_entities']} gold entities | "
        f"{result['predicted_entities']} predicted"
    )
    for mode in ("strict", "partial"):
        print(f"\n-- {mode} span matching --")
        print(f"{'type':<16}{'TP':>5}{'FP':>5}{'FN':>5}{'prec':>8}{'rec':>8}{'F1':>8}")
        for pii_type, counts in result[mode].items():
            print(
                f"{pii_type:<16}{counts['tp']:>5}{counts['fp']:>5}{counts['fn']:>5}"
                f"{_fmt(counts['precision']):>8}{_fmt(counts['recall']):>8}{_fmt(counts['f1']):>8}"
            )
        micro = result[f"micro_{mode}"]
        print(
            f"{'MICRO':<16}{micro['tp']:>5}{micro['fp']:>5}{micro['fn']:>5}"
            f"{_fmt(micro['precision']):>8}{_fmt(micro['recall']):>8}{_fmt(micro['f1']):>8}"
        )
    tokens = result["token_level"]
    print(
        f"\n-- token level --\n"
        f"tokens={tokens['tp'] + tokens['fp'] + tokens['fn'] + tokens['tn']} "
        f"TP={tokens['tp']} FP={tokens['fp']} FN={tokens['fn']} TN={tokens['tn']}\n"
        f"accuracy={_fmt(tokens['accuracy'])} precision={_fmt(tokens['precision'])} "
        f"recall={_fmt(tokens['recall'])} F1={_fmt(tokens['f1'])}"
    )


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-ner", action="store_true", help="score the rules-only pipeline")
    parser.add_argument("--ner-orgs", action="store_true", help="enable spaCy ORG detections")
    parser.add_argument("--out", type=Path, default=HERE / "results.json")
    parser.add_argument(
        "--docx",
        type=Path,
        default=HERE.parent / "data" / "input" / "Red Herring Prospectus.docx",
        help="source document, so the prospectus sample is scored in full context",
    )
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="score the sample on its own instead of within the whole document",
    )
    args = parser.parse_args(argv)

    config = RedactionConfig()
    if args.no_ner:
        config.spacy_model = None
    if args.ner_orgs:
        config.ner_organizations = True

    context = None if args.isolated or not args.docx.exists() else args.docx

    results = []
    for name, ctx in (
        ("prospectus_gold.json", context),
        ("holdout_gold.json", context),
        ("synthetic_gold.json", None),  # self-contained: no wider document exists
    ):
        path = GOLD_DIR / name
        if not path.exists():
            print(f"missing {path}; run eval/build_gold.py first", file=sys.stderr)
            return 1
        result = evaluate(path, Redactor(config), ctx)
        print_report(result)
        results.append(result)

    payload = {
        "config": {
            "spacy_model": config.spacy_model,
            "ner_organizations": config.ner_organizations,
            "enable_propagation": config.enable_propagation,
            "min_score": config.min_score,
            "redact_public_institutions": config.redact_public_institutions,
        },
        "suites": results,
    }
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
