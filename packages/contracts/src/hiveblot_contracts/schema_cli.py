"""Generate or verify committed HiveBlot contract schemas."""

from __future__ import annotations

import argparse
from pathlib import Path

from .registry import rendered_schema_snapshots

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "schemas" / "v1"


def check_snapshots(output_dir: Path) -> list[str]:
    expected = rendered_schema_snapshots()
    actual_names = {path.name for path in output_dir.glob("*.schema.json")}
    problems = [
        f"unexpected schema snapshot: {name}" for name in sorted(actual_names - expected.keys())
    ]
    for name, content in expected.items():
        path = output_dir / name
        if not path.is_file():
            problems.append(f"missing schema snapshot: {name}")
        elif path.read_text(encoding="utf-8") != content:
            problems.append(f"outdated schema snapshot: {name}")
    return problems


def write_snapshots(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    expected = rendered_schema_snapshots()
    for old_path in output_dir.glob("*.schema.json"):
        if old_path.name not in expected:
            old_path.unlink()
    for name, content in expected.items():
        (output_dir / name).write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when snapshots differ")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.check:
        problems = check_snapshots(args.output_dir)
        if problems:
            parser.exit(1, "\n".join(problems) + "\n")
        return
    write_snapshots(args.output_dir)
