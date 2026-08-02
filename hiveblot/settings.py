"""Validated runtime configuration for local, test, and deployed environments."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class RuntimeEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    DEPLOYED = "deployed"


class Settings(BaseSettings):
    """HiveBlot settings loaded from environment variables or a local `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        strict=True,
        validate_default=True,
    )

    environment: RuntimeEnvironment = Field(
        default=RuntimeEnvironment.LOCAL,
        validation_alias="HIVEBLOT_ENV",
    )
    database_url: str = Field(
        default="postgresql://localhost:5432/hiveblot",
        validation_alias="DATABASE_URL",
        min_length=1,
    )
    vllm_base_url: str = Field(
        default="http://localhost:8000/v1",
        validation_alias="VLLM_BASE_URL",
        min_length=1,
    )
    vllm_model: str = Field(
        default="Qwen/Qwen3-VL-8B-Instruct",
        validation_alias="VLLM_MODEL",
        min_length=1,
    )
    vllm_model_revision: str = Field(
        default="local-cache-unpinned",
        validation_alias="VLLM_MODEL_REVISION",
        min_length=1,
    )
    vllm_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="VLLM_API_KEY",
    )
    vllm_timeout_seconds: float = Field(
        default=300,
        validation_alias="VLLM_TIMEOUT_SECONDS",
        gt=0,
        le=3600,
    )
    vllm_max_tokens: int = Field(
        default=4096,
        validation_alias="VLLM_MAX_TOKENS",
        ge=1,
        le=65536,
    )
    vllm_image_max_side: int = Field(
        default=1800,
        validation_alias="VLLM_IMAGE_MAX_SIDE",
        ge=128,
        le=16384,
    )
    data_dir: Path = Field(default=Path("data"), validation_alias="HIVEBLOT_DATA_DIR")
    s3_endpoint_url: str = Field(
        default="http://localhost:9000",
        validation_alias="S3_ENDPOINT_URL",
        min_length=1,
    )
    s3_region: str = Field(default="us-east-1", validation_alias="S3_REGION", min_length=1)
    s3_bucket: str = Field(
        default="hiveblot-artifacts",
        validation_alias="S3_BUCKET",
        min_length=3,
        max_length=63,
    )
    s3_access_key_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="S3_ACCESS_KEY_ID",
    )
    s3_secret_access_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="S3_SECRET_ACCESS_KEY",
    )
    artifact_signed_url_seconds: int = Field(
        default=900,
        validation_alias="ARTIFACT_SIGNED_URL_SECONDS",
        ge=60,
        le=604800,
    )
    artifact_upload_url_seconds: int = Field(
        default=3600,
        validation_alias="ARTIFACT_UPLOAD_URL_SECONDS",
        ge=60,
        le=604800,
    )
    artifact_max_bytes: int = Field(
        default=1_073_741_824,
        validation_alias="ARTIFACT_MAX_BYTES",
        ge=1,
    )
    job_lease_seconds: int = Field(
        default=60,
        validation_alias="JOB_LEASE_SECONDS",
        ge=5,
        le=3600,
    )
    job_worker_poll_seconds: float = Field(
        default=1.0,
        validation_alias="JOB_WORKER_POLL_SECONDS",
        gt=0,
        le=60,
    )
    job_worker_id: str = Field(
        default="local-worker",
        validation_alias="JOB_WORKER_ID",
        min_length=1,
        max_length=200,
    )
    job_docker_binary: str = Field(
        default="docker",
        validation_alias="JOB_DOCKER_BINARY",
        min_length=1,
        max_length=1000,
    )
    job_max_log_bytes: int = Field(
        default=262_144,
        validation_alias="JOB_MAX_LOG_BYTES",
        ge=1024,
        le=16_777_216,
    )
    job_max_output_bytes: int = Field(
        default=1_073_741_824,
        validation_alias="JOB_MAX_OUTPUT_BYTES",
        ge=1,
    )
    source_ingest_allowed_hosts: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        validation_alias="SOURCE_INGEST_ALLOWED_HOSTS",
    )
    pdf_dpi: int = Field(default=350, validation_alias="PDF_DPI", ge=72, le=1200)
    min_candidate_score: float = Field(
        default=0.35,
        validation_alias="MIN_CANDIDATE_SCORE",
        ge=0,
        le=1,
    )
    min_vlm_score: float = Field(
        default=0.65,
        validation_alias="MIN_VLM_SCORE",
        ge=0,
        le=1,
    )

    @field_validator("vllm_base_url")
    @classmethod
    def strip_base_url_suffix(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("s3_endpoint_url")
    @classmethod
    def strip_s3_endpoint_suffix(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("source_ingest_allowed_hosts", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip().casefold() for part in value.split(",") if part.strip())
        return value

    @field_validator("data_dir")
    @classmethod
    def resolve_data_directory(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def deployed_configuration_must_be_explicit_and_safe(self) -> Self:
        if self.environment is not RuntimeEnvironment.DEPLOYED:
            return self

        required = {
            "database_url",
            "vllm_base_url",
            "vllm_model",
            "vllm_model_revision",
            "vllm_api_key",
            "s3_endpoint_url",
            "s3_bucket",
            "s3_access_key_id",
            "s3_secret_access_key",
        }
        missing = sorted(required - self.model_fields_set)
        if missing:
            raise ValueError(
                "deployed configuration requires explicit values for: " + ", ".join(missing)
            )

        key = self.vllm_api_key.get_secret_value().strip().casefold()
        if key in {"", "local", "replace-me", "changeme"}:
            raise ValueError("VLLM_API_KEY must not use a local or placeholder value when deployed")
        if "localhost" in self.database_url.casefold() or "127.0.0.1" in self.database_url:
            raise ValueError("DATABASE_URL must not point at localhost when deployed")
        if "localhost" in self.vllm_base_url or "127.0.0.1" in self.vllm_base_url:
            raise ValueError("VLLM_BASE_URL must not point at localhost when deployed")
        storage_key = self.s3_secret_access_key.get_secret_value().strip().casefold()
        if storage_key in {"", "minioadmin", "replace-me", "changeme"}:
            raise ValueError(
                "S3_SECRET_ACCESS_KEY must not use a local or placeholder value when deployed"
            )
        return self

    @classmethod
    def from_env(cls, **overrides: Any) -> Settings:
        """Compatibility constructor retained for the hackathon ingestion CLI."""

        return cls(**overrides)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
