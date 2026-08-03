"""Render the minimal Kubernetes runtime Secret input from an ignored local environment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import quote


def parse_environment(content: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        result[name.strip()] = value.strip()
    return result


def render_secret_environment(content: str) -> str:
    values = parse_environment(content)
    required = {
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "VLLM_API_KEY",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "AUTH_TOKEN_PEPPER",
    }
    missing = sorted(name for name in required if not values.get(name))
    if missing:
        raise ValueError("local environment is missing values for: " + ", ".join(missing))

    database_url = (
        "postgresql://"
        f"{quote(values['POSTGRES_USER'], safe='')}:"
        f"{quote(values['POSTGRES_PASSWORD'], safe='')}"
        f"@postgres:5432/{quote(values['POSTGRES_DB'], safe='')}"
    )
    secret_values = {
        "DATABASE_URL": database_url,
        "AUTH_TOKEN_PEPPER": values["AUTH_TOKEN_PEPPER"],
        "VLLM_API_KEY": values["VLLM_API_KEY"],
        "S3_ACCESS_KEY_ID": values["S3_ACCESS_KEY_ID"],
        "S3_SECRET_ACCESS_KEY": values["S3_SECRET_ACCESS_KEY"],
        "TEMPORAL_API_KEY": values.get("TEMPORAL_API_KEY", ""),
    }
    return "".join(f"{name}={value}\n" for name, value in secret_values.items())


def write_secret_environment(source: Path, output: Path) -> None:
    rendered = render_secret_environment(source.read_text(encoding="utf-8"))
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(rendered)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    write_secret_environment(arguments.input, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
