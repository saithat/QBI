"""Finite container entry points; domain behavior remains outside the job service."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from hiveblot.records import flatten_records


def normalize_legacy_western_blot(
    input_path: Path,
    output_path: Path,
    *,
    source_pdf: str | None = None,
) -> int:
    """Preserve the hackathon model-output normalizer behind a finite operation."""

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        figures = [payload]
    elif isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        figures = payload
    else:
        raise ValueError("legacy model output must be a JSON object or array of objects")
    records = flatten_records(figures, source_pdf=source_pdf)
    output_path.write_text(
        json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return len(records)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one bounded HiveBlot worker operation")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    normalize = subparsers.add_parser(
        "western-blot-normalize",
        help="normalize preserved legacy western-blot model output",
    )
    normalize.add_argument("--input", type=Path, default=Path("/inputs/model_output"))
    normalize.add_argument("--output", type=Path, default=Path("/outputs/records"))
    normalize.add_argument("--source-pdf")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.operation == "western-blot-normalize":
        count = normalize_legacy_western_blot(
            arguments.input,
            arguments.output,
            source_pdf=arguments.source_pdf,
        )
        print(json.dumps({"normalized_records": count}, sort_keys=True))
        return 0
    raise AssertionError("argparse accepted an unknown operation")


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
