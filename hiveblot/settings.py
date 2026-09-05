"""Database, source files, and maintainer extraction settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        strict=True,
        validate_default=True,
    )

    database_url: str = Field(
        default="postgresql://localhost:5432/hiveblot", validation_alias="DATABASE_URL"
    )
    data_dir: Path = Field(default=Path("data"), validation_alias="HIVEBLOT_DATA_DIR")
    vllm_base_url: str = Field(default="http://localhost:8000/v1", validation_alias="VLLM_BASE_URL")
    vllm_model: str = Field(
        default="Qwen/Qwen3-VL-8B-Instruct", validation_alias="VLLM_MODEL", min_length=1
    )
    vllm_model_revision: str = Field(
        default="local-cache-unpinned", validation_alias="VLLM_MODEL_REVISION", min_length=1
    )
    vllm_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="VLLM_API_KEY")
    vllm_timeout_seconds: float = Field(
        default=300, validation_alias="VLLM_TIMEOUT_SECONDS", gt=0, le=3600
    )
    vllm_max_tokens: int = Field(default=4096, validation_alias="VLLM_MAX_TOKENS", ge=1, le=65536)
    vllm_image_max_side: int = Field(
        default=1800, validation_alias="VLLM_IMAGE_MAX_SIDE", ge=128, le=16384
    )
    pdf_dpi: int = Field(default=350, validation_alias="PDF_DPI", ge=72, le=1200)
    min_candidate_score: float = Field(
        default=0.35, validation_alias="MIN_CANDIDATE_SCORE", ge=0, le=1
    )
    min_vlm_score: float = Field(default=0.65, validation_alias="MIN_VLM_SCORE", ge=0, le=1)

    @field_validator("database_url")
    @classmethod
    def postgres_url(cls, value: str) -> str:
        if urlsplit(value).scheme not in {"postgresql", "postgres"}:
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
        return value

    @field_validator("vllm_base_url")
    @classmethod
    def model_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("VLLM_BASE_URL must be an HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("data_dir")
    @classmethod
    def resolve_data_directory(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @classmethod
    def from_env(cls, **overrides: Any) -> Settings:
        return cls(**overrides)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
