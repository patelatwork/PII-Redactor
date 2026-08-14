"""Command-line interface.

    piiredact redact  INPUT -o OUTPUT [--seed S] [--types T,...]
    piiredact analyze INPUT [--json]

Deliberately argparse-only: no CLI framework dependency, so the container image
and the "no extras" install stay small.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .config import RedactionConfig
from .documents import redact_document
from .redactor import Redactor
from .types import PIIType


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="piiredact",
        description="Detect and replace personal data in .docx / .pdf / .txt documents.",
    )
    parser.add_argument("--version", action="version", version=f"piiredact {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("input", type=Path, help="source document")
    common.add_argument("--config", type=Path, help="YAML config file")
    common.add_argument("--seed", help="surrogate seed (same seed => same fake values)")
    common.add_argument(
        "--types",
        help="comma-separated PII types to redact (default: all). "
        f"One of: {','.join(t.value for t in PIIType)}",
    )
    common.add_argument(
        "--no-ner",
        action="store_true",
        help="skip the spaCy layer (faster, rules only)",
    )
    common.add_argument(
        "--no-propagation",
        action="store_true",
        help="disable the document-wide second pass",
    )
    common.add_argument(
        "--redact-public-institutions",
        action="store_true",
        help="also redact regulators and exchanges (SEBI, BSE, ...)",
    )

    redact = sub.add_parser("redact", parents=[common], help="write a redacted document")
    redact.add_argument("-o", "--output", type=Path, required=True, help="output .docx/.txt")
    redact.add_argument(
        "--report-dir",
        type=Path,
        help="directory for mapping.json / entities.csv / summary.json "
        "(default: alongside the output)",
    )
    redact.add_argument(
        "--no-mapping",
        action="store_true",
        help="do not write the reversible original->surrogate map",
    )

    analyze = sub.add_parser("analyze", parents=[common], help="detect only, write no document")
    analyze.add_argument("--json", action="store_true", help="emit entities as JSON")
    analyze.add_argument("--limit", type=int, default=40, help="rows to print (table mode)")
    return parser


def config_from_args(args: argparse.Namespace) -> RedactionConfig:
    config = RedactionConfig.from_yaml(args.config) if args.config else RedactionConfig.from_env()
    if args.seed:
        config.seed = args.seed
    if args.types:
        config.enabled_types = tuple(
            PIIType(t.strip().upper()) for t in args.types.split(",") if t.strip()
        )
    if args.no_ner:
        config.spacy_model = None
    if args.no_propagation:
        config.enable_propagation = False
    if args.redact_public_institutions:
        config.redact_public_institutions = True
    if getattr(args, "no_mapping", False):
        config.emit_mapping = False
    return config


def cmd_redact(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    redactor = Redactor(config)

    started = time.perf_counter()
    entities = redact_document(args.input, args.output, redactor)
    elapsed = time.perf_counter() - started

    report_dir = args.report_dir or args.output.parent
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = redactor.summarise(entities)

    (report_dir / "summary.json").write_text(
        json.dumps(
            {
                "input": str(args.input),
                "output": str(args.output),
                "seed": config.seed,
                "entities": len(entities),
                "by_type": summary,
                "seconds": round(elapsed, 2),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with (report_dir / "entities.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["type", "start", "end", "text", "recognizer", "score", "notes"]
        )
        writer.writeheader()
        for entity in entities:
            writer.writerow(entity.as_dict())

    if config.emit_mapping:
        (report_dir / "mapping.json").write_text(
            json.dumps(redactor.mapping_rows(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print(f"Redacted {args.input} -> {args.output} in {elapsed:.1f}s")
    print(f"{len(entities)} entities replaced:")
    for pii_type, count in sorted(summary.items(), key=lambda kv: -kv[1]):
        print(f"  {pii_type:<16} {count:>6}")
    print(f"Reports in {report_dir}")
    if config.emit_mapping:
        print("NOTE: mapping.json re-identifies every redacted value. Store it accordingly.")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    from .documents import analyze_segments, pdf_io

    config = config_from_args(args)
    redactor = Redactor(config)

    suffix = args.input.suffix.lower()
    if suffix == ".pdf":
        segments = pdf_io.extract_blocks(args.input)
    elif suffix == ".docx":
        from .documents.docx_io import iter_paragraphs, load, paragraph_text

        segments = [paragraph_text(p) for p in iter_paragraphs(load(args.input))]
    else:
        segments = args.input.read_text(encoding="utf-8", errors="replace").split("\n\n")

    entities, _ = analyze_segments(redactor, segments)

    if args.json:
        json.dump([e.as_dict() for e in entities], sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0

    print(f"{len(entities)} entities")
    for pii_type, count in sorted(redactor.summarise(entities).items(), key=lambda kv: -kv[1]):
        print(f"  {pii_type:<16} {count:>6}")
    print()
    for entity in entities[: args.limit]:
        preview = " ".join(entity.text.split())[:60]
        print(f"  {entity.type.value:<14} {entity.score:.2f} {entity.recognizer:<20} {preview}")
    if len(entities) > args.limit:
        print(f"  ... {len(entities) - args.limit} more")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return {"redact": cmd_redact, "analyze": cmd_analyze}[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
