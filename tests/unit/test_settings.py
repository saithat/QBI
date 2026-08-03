from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from hiveblot.settings import RuntimeEnvironment, Settings


def test_local_settings_do_not_embed_fixed_credentials() -> None:
    settings = Settings(_env_file=None)

    assert urlsplit(settings.database_url).password is None
    assert settings.vllm_api_key.get_secret_value() == ""
    assert settings.s3_access_key_id.get_secret_value() == ""
    assert settings.s3_secret_access_key.get_secret_value() == ""


def test_settings_parse_an_explicit_test_environment(monkeypatch, tmp_path) -> None:
    model_api_key = token_urlsafe(18)
    monkeypatch.setenv("HIVEBLOT_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5433/test")
    monkeypatch.setenv("VLLM_BASE_URL", "http://model.test/v1/")
    monkeypatch.setenv("VLLM_API_KEY", model_api_key)
    monkeypatch.setenv("HIVEBLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOURCE_INGEST_ALLOWED_HOSTS", "repo.example, data.example")

    settings = Settings(_env_file=None)

    assert settings.environment is RuntimeEnvironment.TEST
    assert settings.vllm_base_url == "http://model.test/v1"
    assert settings.data_dir == Path(tmp_path).resolve()
    assert settings.source_ingest_allowed_hosts == ("repo.example", "data.example")
    assert model_api_key not in repr(settings)


def test_empty_optional_kubernetes_placement_values_are_unset() -> None:
    settings = Settings(
        kubernetes_gpu_node_selector_key="",
        kubernetes_gpu_node_selector_value="",
        kubernetes_gpu_toleration_key="",
        _env_file=None,
    )

    assert settings.kubernetes_gpu_node_selector_key is None
    assert settings.kubernetes_gpu_node_selector_value is None
    assert settings.kubernetes_gpu_toleration_key is None


def test_deployed_settings_require_explicit_non_local_values(monkeypatch) -> None:
    for name in (
        "HIVEBLOT_ENV",
        "DATABASE_URL",
        "VLLM_BASE_URL",
        "VLLM_MODEL",
        "VLLM_MODEL_REVISION",
        "VLLM_API_KEY",
        "S3_ENDPOINT_URL",
        "S3_BUCKET",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "TEMPORAL_ADDRESS",
        "TEMPORAL_NAMESPACE",
        "TEMPORAL_TASK_QUEUE",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError, match="deployed configuration requires explicit"):
        Settings(environment=RuntimeEnvironment.DEPLOYED, _env_file=None)

    with pytest.raises(ValidationError, match="placeholder"):
        Settings(
            environment=RuntimeEnvironment.DEPLOYED,
            database_url="postgresql://postgres.example/hiveblot",
            vllm_base_url="https://models.example/v1",
            vllm_model="deployed-model",
            vllm_model_revision="immutable-model-revision",
            vllm_api_key="",
            s3_endpoint_url="https://objects.example",
            s3_bucket="hiveblot-production",
            s3_access_key_id="",
            s3_secret_access_key="",
            temporal_address="temporal.example:7233",
            temporal_namespace="hiveblot",
            temporal_task_queue="hiveblot-platform-v1",
            _env_file=None,
        )
