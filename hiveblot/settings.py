"""Validated runtime configuration for local, test, and deployed environments."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class RuntimeEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    DEPLOYED = "deployed"


class JobExecutorKind(StrEnum):
    LOCAL_DOCKER = "local-docker"
    KUBERNETES_JOB = "kubernetes-job"


class AuthenticationMode(StrEnum):
    DISABLED = "disabled"
    BEARER = "bearer"


class DiscoverySettings(BaseSettings):
    """Least-privilege settings for the bounded public-discovery worker."""

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
    artifact_max_bytes: int = Field(
        default=1_073_741_824,
        validation_alias="ARTIFACT_MAX_BYTES",
        ge=1,
    )
    pmc_oai_base_url: str = Field(
        default="https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/",
        validation_alias="PMC_OAI_BASE_URL",
        min_length=1,
        max_length=4096,
    )
    discovery_user_agent: str = Field(
        default="HiveBlot local development (contact not configured)",
        validation_alias="DISCOVERY_USER_AGENT",
        min_length=10,
        max_length=500,
    )
    discovery_http_timeout_seconds: float = Field(
        default=30,
        validation_alias="DISCOVERY_HTTP_TIMEOUT_SECONDS",
        gt=0,
        le=300,
    )
    discovery_max_response_bytes: int = Field(
        default=5_000_000,
        validation_alias="DISCOVERY_MAX_RESPONSE_BYTES",
        ge=1024,
        le=100_000_000,
    )
    discovery_lookback_days: int = Field(
        default=2,
        validation_alias="DISCOVERY_LOOKBACK_DAYS",
        ge=0,
        le=365,
    )
    discovery_maximum_pages: int = Field(
        default=20,
        validation_alias="DISCOVERY_MAXIMUM_PAGES",
        ge=1,
        le=1000,
    )

    @field_validator("pmc_oai_base_url")
    @classmethod
    def normalize_pmc_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("PMC_OAI_BASE_URL must be an HTTP(S) URL with a host")
        return value.rstrip("/") + "/"

    @field_validator("s3_endpoint_url")
    @classmethod
    def normalize_s3_endpoint(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def deployed_discovery_configuration_is_explicit(self) -> Self:
        if self.discovery_max_response_bytes > self.artifact_max_bytes:
            raise ValueError("DISCOVERY_MAX_RESPONSE_BYTES cannot exceed ARTIFACT_MAX_BYTES")
        if self.environment is not RuntimeEnvironment.DEPLOYED:
            return self
        required = {
            "database_url",
            "s3_endpoint_url",
            "s3_bucket",
            "s3_access_key_id",
            "s3_secret_access_key",
            "discovery_user_agent",
        }
        missing = sorted(required - self.model_fields_set)
        if missing:
            raise ValueError(
                "deployed discovery requires explicit values for: " + ", ".join(missing)
            )
        if "contact not configured" in self.discovery_user_agent.casefold():
            raise ValueError("DISCOVERY_USER_AGENT must identify an operator contact when deployed")
        if "localhost" in self.database_url.casefold() or "127.0.0.1" in self.database_url:
            raise ValueError("DATABASE_URL must not point at localhost when deployed")
        if "localhost" in self.s3_endpoint_url.casefold() or "127.0.0.1" in self.s3_endpoint_url:
            raise ValueError("S3_ENDPOINT_URL must not point at localhost when deployed")
        storage_key = self.s3_secret_access_key.get_secret_value().strip().casefold()
        if storage_key in {"", "minioadmin", "replace-me", "changeme"}:
            raise ValueError(
                "S3_SECRET_ACCESS_KEY must not use a local or placeholder value when deployed"
            )
        return self


class FetchWorkerSettings(BaseSettings):
    """Least-privilege settings for long-lived public fetch workers."""

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
    artifact_max_bytes: int = Field(
        default=1_073_741_824,
        validation_alias="ARTIFACT_MAX_BYTES",
        ge=1,
    )
    fetch_worker_id: str = Field(
        default="fetch-worker-local",
        validation_alias="FETCH_WORKER_ID",
        min_length=1,
        max_length=200,
    )
    fetch_worker_poll_seconds: float = Field(
        default=1,
        validation_alias="FETCH_WORKER_POLL_SECONDS",
        gt=0,
        le=300,
    )
    fetch_lease_seconds: int = Field(
        default=120,
        validation_alias="FETCH_LEASE_SECONDS",
        ge=5,
        le=3600,
    )
    fetch_max_attempts: int = Field(
        default=5,
        validation_alias="FETCH_MAX_ATTEMPTS",
        ge=1,
        le=100,
    )
    fetch_http_timeout_seconds: float = Field(
        default=30,
        validation_alias="FETCH_HTTP_TIMEOUT_SECONDS",
        gt=0,
        le=300,
    )
    fetch_max_response_bytes: int = Field(
        default=250_000_000,
        validation_alias="FETCH_MAX_RESPONSE_BYTES",
        ge=1024,
        le=2_000_000_000,
    )
    fetch_max_redirects: int = Field(
        default=5,
        validation_alias="FETCH_MAX_REDIRECTS",
        ge=0,
        le=20,
    )
    fetch_domain_minimum_interval_milliseconds: int = Field(
        default=1000,
        validation_alias="FETCH_DOMAIN_MINIMUM_INTERVAL_MILLISECONDS",
        ge=0,
        le=3_600_000,
    )
    fetch_domain_maximum_concurrency: int = Field(
        default=2,
        validation_alias="FETCH_DOMAIN_MAXIMUM_CONCURRENCY",
        ge=1,
        le=1000,
    )
    fetch_domain_permit_seconds: int = Field(
        default=60,
        validation_alias="FETCH_DOMAIN_PERMIT_SECONDS",
        ge=1,
        le=3600,
    )
    fetch_domain_maximum_wait_seconds: float = Field(
        default=30,
        validation_alias="FETCH_DOMAIN_MAXIMUM_WAIT_SECONDS",
        gt=0,
        le=3600,
    )
    fetch_robots_cache_seconds: int = Field(
        default=86_400,
        validation_alias="FETCH_ROBOTS_CACHE_SECONDS",
        ge=60,
        le=2_592_000,
    )
    fetch_robots_max_bytes: int = Field(
        default=524_288,
        validation_alias="FETCH_ROBOTS_MAX_BYTES",
        ge=1024,
        le=5_000_000,
    )
    discovery_user_agent: str = Field(
        default="HiveBlot local development (contact not configured)",
        validation_alias="DISCOVERY_USER_AGENT",
        min_length=10,
        max_length=500,
    )
    fetch_allowed_hosts: Annotated[tuple[str, ...], NoDecode] = Field(
        default=("pmc.ncbi.nlm.nih.gov",),
        validation_alias="FETCH_ALLOWED_HOSTS",
        min_length=1,
    )

    @field_validator("s3_endpoint_url")
    @classmethod
    def normalize_fetch_s3_endpoint(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("fetch_allowed_hosts", mode="before")
    @classmethod
    def parse_fetch_allowed_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip().casefold() for part in value.split(",") if part.strip())
        return value

    @field_validator("fetch_allowed_hosts")
    @classmethod
    def validate_fetch_allowed_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(host.strip().casefold() for host in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("FETCH_ALLOWED_HOSTS entries must be unique")
        for host in normalized:
            if urlsplit(f"//{host}").hostname != host or any(
                character.isspace() for character in host
            ):
                raise ValueError("FETCH_ALLOWED_HOSTS entries must be exact host names")
        return normalized

    @model_validator(mode="after")
    def fetch_configuration_is_safe(self) -> Self:
        if self.fetch_max_response_bytes > self.artifact_max_bytes:
            raise ValueError("FETCH_MAX_RESPONSE_BYTES cannot exceed ARTIFACT_MAX_BYTES")
        if self.fetch_http_timeout_seconds >= self.fetch_lease_seconds:
            raise ValueError("FETCH_HTTP_TIMEOUT_SECONDS must be shorter than FETCH_LEASE_SECONDS")
        if self.environment is not RuntimeEnvironment.DEPLOYED:
            return self
        required = {
            "database_url",
            "s3_endpoint_url",
            "s3_bucket",
            "s3_access_key_id",
            "s3_secret_access_key",
            "discovery_user_agent",
            "fetch_worker_id",
            "fetch_allowed_hosts",
        }
        missing = sorted(required - self.model_fields_set)
        if missing:
            raise ValueError(
                "deployed fetch workers require explicit values for: " + ", ".join(missing)
            )
        if "contact not configured" in self.discovery_user_agent.casefold():
            raise ValueError("DISCOVERY_USER_AGENT must identify an operator contact when deployed")
        if "localhost" in self.database_url.casefold() or "127.0.0.1" in self.database_url:
            raise ValueError("DATABASE_URL must not point at localhost when deployed")
        if "localhost" in self.s3_endpoint_url.casefold() or "127.0.0.1" in self.s3_endpoint_url:
            raise ValueError("S3_ENDPOINT_URL must not point at localhost when deployed")
        if not self.s3_access_key_id.get_secret_value().strip():
            raise ValueError("S3_ACCESS_KEY_ID must not be empty when deployed")
        storage_key = self.s3_secret_access_key.get_secret_value().strip().casefold()
        if storage_key in {"", "minioadmin", "replace-me", "changeme"}:
            raise ValueError(
                "S3_SECRET_ACCESS_KEY must not use a local or placeholder value when deployed"
            )
        return self


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
    authentication_mode: AuthenticationMode = Field(
        default=AuthenticationMode.DISABLED,
        validation_alias="AUTHENTICATION_MODE",
    )
    auth_token_pepper: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="AUTH_TOKEN_PEPPER",
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
    job_executor: JobExecutorKind = Field(
        default=JobExecutorKind.LOCAL_DOCKER,
        validation_alias="JOB_EXECUTOR",
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
    kubernetes_job_namespace: str = Field(
        default="hiveblot",
        validation_alias="KUBERNETES_JOB_NAMESPACE",
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$",
    )
    kubernetes_workspace_pvc: str = Field(
        default="hiveblot-job-workspaces",
        validation_alias="KUBERNETES_WORKSPACE_PVC",
        min_length=1,
        max_length=253,
        pattern=r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$",
    )
    kubernetes_workspace_root: Path = Field(
        default=Path("/var/lib/hiveblot/jobs"),
        validation_alias="KUBERNETES_WORKSPACE_ROOT",
    )
    kubernetes_job_service_account: str = Field(
        default="hiveblot-job-runner",
        validation_alias="KUBERNETES_JOB_SERVICE_ACCOUNT",
        min_length=1,
        max_length=253,
        pattern=r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$",
    )
    kubernetes_poll_seconds: float = Field(
        default=1,
        validation_alias="KUBERNETES_POLL_SECONDS",
        gt=0,
        le=60,
    )
    kubernetes_api_timeout_seconds: float = Field(
        default=15,
        validation_alias="KUBERNETES_API_TIMEOUT_SECONDS",
        gt=0,
        le=120,
    )
    kubernetes_job_ttl_seconds: int = Field(
        default=3600,
        validation_alias="KUBERNETES_JOB_TTL_SECONDS",
        ge=0,
        le=604_800,
    )
    kubernetes_gpu_node_selector_key: str | None = Field(
        default=None,
        validation_alias="KUBERNETES_GPU_NODE_SELECTOR_KEY",
        min_length=1,
        max_length=253,
    )
    kubernetes_gpu_node_selector_value: str | None = Field(
        default=None,
        validation_alias="KUBERNETES_GPU_NODE_SELECTOR_VALUE",
        min_length=1,
        max_length=63,
    )
    kubernetes_gpu_toleration_key: str | None = Field(
        default=None,
        validation_alias="KUBERNETES_GPU_TOLERATION_KEY",
        min_length=1,
        max_length=253,
    )
    kubernetes_gpu_toleration_effect: str = Field(
        default="NoSchedule",
        validation_alias="KUBERNETES_GPU_TOLERATION_EFFECT",
        pattern=r"^(NoSchedule|PreferNoSchedule|NoExecute)$",
    )
    temporal_address: str = Field(
        default="localhost:7233",
        validation_alias="TEMPORAL_ADDRESS",
        min_length=1,
        max_length=1000,
    )
    temporal_namespace: str = Field(
        default="default",
        validation_alias="TEMPORAL_NAMESPACE",
        min_length=1,
        max_length=255,
    )
    temporal_task_queue: str = Field(
        default="hiveblot-platform-v1",
        validation_alias="TEMPORAL_TASK_QUEUE",
        min_length=1,
        max_length=255,
    )
    temporal_tls: bool = Field(default=False, validation_alias="TEMPORAL_TLS")
    temporal_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="TEMPORAL_API_KEY",
    )
    source_ingest_allowed_hosts: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        validation_alias="SOURCE_INGEST_ALLOWED_HOSTS",
    )
    pmc_oai_base_url: str = Field(
        default="https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/",
        validation_alias="PMC_OAI_BASE_URL",
        min_length=1,
        max_length=4096,
    )
    discovery_user_agent: str = Field(
        default="HiveBlot local development (contact not configured)",
        validation_alias="DISCOVERY_USER_AGENT",
        min_length=10,
        max_length=500,
    )
    discovery_http_timeout_seconds: float = Field(
        default=30,
        validation_alias="DISCOVERY_HTTP_TIMEOUT_SECONDS",
        gt=0,
        le=300,
    )
    discovery_max_response_bytes: int = Field(
        default=5_000_000,
        validation_alias="DISCOVERY_MAX_RESPONSE_BYTES",
        ge=1024,
        le=100_000_000,
    )
    discovery_lookback_days: int = Field(
        default=2,
        validation_alias="DISCOVERY_LOOKBACK_DAYS",
        ge=0,
        le=365,
    )
    discovery_maximum_pages: int = Field(
        default=20,
        validation_alias="DISCOVERY_MAXIMUM_PAGES",
        ge=1,
        le=1000,
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

    @field_validator("pmc_oai_base_url")
    @classmethod
    def normalize_pmc_oai_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("PMC_OAI_BASE_URL must be an HTTP(S) URL with a host")
        return value.rstrip("/") + "/"

    @field_validator("source_ingest_allowed_hosts", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip().casefold() for part in value.split(",") if part.strip())
        return value

    @field_validator(
        "kubernetes_gpu_node_selector_key",
        "kubernetes_gpu_node_selector_value",
        "kubernetes_gpu_toleration_key",
        mode="before",
    )
    @classmethod
    def empty_optional_setting_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("data_dir", "kubernetes_workspace_root")
    @classmethod
    def resolve_data_directory(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def deployed_configuration_must_be_explicit_and_safe(self) -> Self:
        if (
            self.authentication_mode is AuthenticationMode.BEARER
            and len(self.auth_token_pepper.get_secret_value()) < 32
        ):
            raise ValueError("AUTH_TOKEN_PEPPER must contain at least 32 characters")
        if self.environment is not RuntimeEnvironment.DEPLOYED:
            return self

        required = {
            "database_url",
            "authentication_mode",
            "auth_token_pepper",
            "vllm_base_url",
            "vllm_model",
            "vllm_model_revision",
            "vllm_api_key",
            "s3_endpoint_url",
            "s3_bucket",
            "s3_access_key_id",
            "s3_secret_access_key",
            "temporal_address",
            "temporal_namespace",
            "temporal_task_queue",
            "discovery_user_agent",
        }
        missing = sorted(required - self.model_fields_set)
        if missing:
            raise ValueError(
                "deployed configuration requires explicit values for: " + ", ".join(missing)
            )

        if self.authentication_mode is not AuthenticationMode.BEARER:
            raise ValueError("deployed API configuration requires bearer authentication")

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
        if "contact not configured" in self.discovery_user_agent.casefold():
            raise ValueError("DISCOVERY_USER_AGENT must identify an operator contact when deployed")
        return self

    @model_validator(mode="after")
    def kubernetes_gpu_placement_is_consistent(self) -> Self:
        selector = (
            self.kubernetes_gpu_node_selector_key,
            self.kubernetes_gpu_node_selector_value,
        )
        if (selector[0] is None) != (selector[1] is None):
            raise ValueError("Kubernetes GPU node selector key and value must be set together")
        return self

    @classmethod
    def from_env(cls, **overrides: Any) -> Settings:
        """Compatibility constructor retained for the hackathon ingestion CLI."""

        return cls(**overrides)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
