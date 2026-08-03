"""Create a private local environment file without committed credentials."""

from __future__ import annotations

import argparse
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import quote

GENERATED_KEYS = (
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "VLLM_API_KEY",
    "AUTH_TOKEN_PEPPER",
)


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
    object_store_user = "hiveblot-local-" + secrets.token_hex(8)
    object_store_password = secrets.token_urlsafe(32)
    model_api_key = secrets.token_urlsafe(32)
    auth_token_pepper = secrets.token_urlsafe(48)
    postgres_user = template_values.get("POSTGRES_USER", "hiveblot")
    postgres_database = template_values.get("POSTGRES_DB", "hiveblot")

    generated = {
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": (
            "postgresql://"
            f"{quote(postgres_user, safe='')}:{quote(postgres_password, safe='')}"
            f"@localhost:5432/{quote(postgres_database, safe='')}"
        ),
        "MINIO_ROOT_USER": object_store_user,
        "MINIO_ROOT_PASSWORD": object_store_password,
        "S3_ACCESS_KEY_ID": object_store_user,
        "S3_SECRET_ACCESS_KEY": object_store_password,
        "VLLM_API_KEY": model_api_key,
        "AUTH_TOKEN_PEPPER": auth_token_pepper,
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
    parser.add_argument("--template", type=Path, default=Path(".env.example"))
    parser.add_argument("--output", type=Path, default=Path(".env"))
    arguments = parser.parse_args()
    create_local_environment(arguments.template, arguments.output)
    print(f"Created {arguments.output} with private file permissions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
