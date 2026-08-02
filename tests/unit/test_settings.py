from pathlib import Path

import pytest
from pydantic import ValidationError

from hiveblot.settings import RuntimeEnvironment, Settings


def test_settings_parse_an_explicit_test_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HIVEBLOT_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5433/test")
    monkeypatch.setenv("VLLM_BASE_URL", "http://model.test/v1/")
    monkeypatch.setenv("VLLM_API_KEY", "test-only")
    monkeypatch.setenv("HIVEBLOT_DATA_DIR", str(tmp_path))

    settings = Settings(_env_file=None)

    assert settings.environment is RuntimeEnvironment.TEST
    assert settings.vllm_base_url == "http://model.test/v1"
    assert settings.data_dir == Path(tmp_path).resolve()
    assert "test-only" not in repr(settings)


def test_deployed_settings_require_explicit_non_local_values(monkeypatch) -> None:
    for name in (
        "HIVEBLOT_ENV",
        "DATABASE_URL",
        "VLLM_BASE_URL",
        "VLLM_MODEL",
        "VLLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError, match="deployed configuration requires explicit"):
        Settings(environment=RuntimeEnvironment.DEPLOYED, _env_file=None)

    with pytest.raises(ValidationError, match="placeholder"):
        Settings(
            environment=RuntimeEnvironment.DEPLOYED,
            database_url="postgresql://service:secret@postgres.example/hiveblot",
            vllm_base_url="https://models.example/v1",
            vllm_model="deployed-model",
            vllm_api_key="replace-me",
            _env_file=None,
        )
