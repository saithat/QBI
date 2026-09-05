"""Create a private local environment file without committed credentials."""

from __future__ import annotations

import argparse
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import quote

GENERATED_KEYS = ("POSTGRES_PASSWORD", "DATABASE_URL")


def _template_values(template: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in template.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def render_local_environment(template: str) -> str:
    """Fill only credential fields, leaving all other template settings unchanged."""

    template_values = _template_values(template)
    missing = sorted(set(GENERATED_KEYS) - template_values.keys())
    if missing:
        raise ValueError("template is missing generated keys: " + ", ".join(missing))

    populated = sorted(key for key in GENERATED_KEYS if template_values[key])
    if populated:
        raise ValueError("generated keys must be blank in the template: " + ", ".join(populated))

    postgres_password = secrets.token_urlsafe(32)
    postgres_user = template_values.get("POSTGRES_USER", "hiveblot")
    postgres_database = template_values.get("POSTGRES_DB", "hiveblot")

    generated = {
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": (
            "postgresql://"
            f"{quote(postgres_user, safe='')}:{quote(postgres_password, safe='')}"
            f"@localhost:5432/{quote(postgres_database, safe='')}"
        ),
    }

    rendered: list[str] = []
    replacements: set[str] = set()
    for line in template.splitlines(keepends=True):
        candidate = line.rstrip("\r\n")
        if candidate and not candidate.lstrip().startswith("#") and "=" in candidate:
            key = candidate.split("=", 1)[0].strip()
            if key in generated:
                newline = line[len(candidate) :]
                rendered.append(f"{key}={generated[key]}{newline}")
                replacements.add(key)
                continue
        rendered.append(line)

    if replacements != set(GENERATED_KEYS):
        missing_replacements = sorted(set(GENERATED_KEYS) - replacements)
        raise ValueError("failed to replace generated keys: " + ", ".join(missing_replacements))
    return "".join(rendered)


def create_local_environment(template_path: Path, output_path: Path) -> None:
    """Atomically create a mode-0600 environment file and never overwrite one."""

    template = template_path.read_text(encoding="utf-8")
    rendered = render_local_environment(template)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, output_path)
        except FileExistsError as error:
            raise FileExistsError(f"refusing to overwrite existing {output_path}") from error
        os.chmod(output_path, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".env"))
    arguments = parser.parse_args()
    template = arguments.template
    if template is None:
        template = Path(".env.example")
        if not template.is_file():
            template = Path(__file__).with_name("env.example")
    create_local_environment(template, arguments.output)
    for directory in (Path("data/input"), Path("data/runs")):
        directory.mkdir(parents=True, exist_ok=True)
    print(f"Created {arguments.output} with private file permissions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
