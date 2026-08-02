from __future__ import annotations

import importlib.util
import stat
from pathlib import Path
from types import ModuleType

import pytest


def _load_bootstrap_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "bootstrap_local_env.py"
    spec = importlib.util.spec_from_file_location("bootstrap_local_env", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap_module()


def _values(content: str) -> dict[str, str]:
    return {
        key: value
        for line in content.splitlines()
        if line and not line.startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }


def test_tracked_environment_templates_do_not_contain_credentials() -> None:
    root = Path(__file__).parents[2]
    for name in (".env.example", ".env.test.example", ".env.deployed.example"):
        values = _values((root / name).read_text(encoding="utf-8"))
        for key in bootstrap.GENERATED_KEYS:
            if key in values:
                assert values[key] == "", f"{name}:{key} must remain blank"


def test_bootstrap_generates_correlated_private_local_credentials(tmp_path) -> None:
    root = Path(__file__).parents[2]
    output = tmp_path / ".env"

    bootstrap.create_local_environment(root / ".env.example", output)

    values = _values(output.read_text(encoding="utf-8"))
    assert values["POSTGRES_PASSWORD"]
    assert values["POSTGRES_PASSWORD"] in values["DATABASE_URL"]
    assert values["MINIO_ROOT_USER"] == values["S3_ACCESS_KEY_ID"]
    assert values["MINIO_ROOT_PASSWORD"] == values["S3_SECRET_ACCESS_KEY"]
    assert values["VLLM_API_KEY"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_bootstrap_refuses_to_overwrite_an_existing_environment(tmp_path) -> None:
    root = Path(__file__).parents[2]
    output = tmp_path / ".env"
    output.write_text("preserve=true\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        bootstrap.create_local_environment(root / ".env.example", output)

    assert output.read_text(encoding="utf-8") == "preserve=true\n"
