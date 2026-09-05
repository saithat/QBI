import sys
from pathlib import Path
from stat import S_IMODE
from urllib.parse import urlsplit

import pytest

from hiveblot import bootstrap
from hiveblot.settings import Settings

TEMPLATE = Path(__file__).parents[1] / ".env.example"


def test_packaged_bootstrap_creates_private_environment_without_a_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    package = tmp_path / "installed" / "hiveblot"
    package.mkdir(parents=True)
    (package / "env.example").write_bytes(TEMPLATE.read_bytes())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(bootstrap, "__file__", str(package / "bootstrap.py"))
    monkeypatch.setattr(sys, "argv", ["hiveblot-env"])
    output = workspace / ".env"
    assert bootstrap.main() == 0
    values = dict(
        line.split("=", 1)
        for line in output.read_text().splitlines()
        if line and not line.startswith("#")
    )
    settings = Settings(_env_file=output)
    assert values["POSTGRES_PASSWORD"]
    assert urlsplit(settings.database_url).password == values["POSTGRES_PASSWORD"]
    assert S_IMODE(output.stat().st_mode) == 0o600
    assert (workspace / "data/input").is_dir() and (workspace / "data/runs").is_dir()
    original = output.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        bootstrap.main()
    assert output.read_bytes() == original


def test_public_catalog_settings_need_no_model_credentials(tmp_path: Path) -> None:
    settings = Settings(
        database_url="postgresql://localhost/catalog", data_dir=tmp_path, _env_file=None
    )
    assert settings.data_dir == tmp_path
    assert settings.vllm_api_key.get_secret_value() == ""
