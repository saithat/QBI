"""Reproducible command-line scoring for frozen evaluation inputs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from hiveblot_contracts import EvaluationScoringInput
from pydantic import ValidationError

from .metrics import evaluation_scoring_input_sha256, score_evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hiveblot-evaluate",
        description="Score one pipeline submission against an immutable HiveBlot dataset snapshot.",
    )
    parser.add_argument("input", type=Path, help="EvaluationScoringInput JSON file")
    parser.add_argument(
        "--output",
        type=Path,
        help="Write EvaluationMetricRunRecord JSON here instead of stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        scoring_input = EvaluationScoringInput.model_validate_json(
            arguments.input.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        parser.error(str(exc))
    input_sha256 = evaluation_scoring_input_sha256(scoring_input)
    record = score_evaluation(
        scoring_input,
        metric_run_id=uuid5(NAMESPACE_URL, f"urn:hiveblot:metric-run:{input_sha256}"),
        created_at=scoring_input.dataset.frozen_at,
    )
    rendered = record.model_dump_json(indent=2) + "\n"
    if arguments.output is None:
        sys.stdout.write(rendered)
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
