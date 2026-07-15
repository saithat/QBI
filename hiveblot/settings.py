from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str
    vllm_base_url: str
    vllm_model: str
    vllm_api_key: str
    vllm_timeout_seconds: float
    vllm_max_tokens: int
    vllm_image_max_side: int
    data_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://hiveblot:hiveblot@localhost:5432/hiveblot",
            ),
            vllm_base_url=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1").rstrip("/"),
            vllm_model=os.getenv("VLLM_MODEL", "Qwen/Qwen3-VL-8B-Instruct"),
            vllm_api_key=os.getenv("VLLM_API_KEY", "local"),
            vllm_timeout_seconds=float(os.getenv("VLLM_TIMEOUT_SECONDS", "300")),
            vllm_max_tokens=int(os.getenv("VLLM_MAX_TOKENS", "2048")),
            vllm_image_max_side=int(os.getenv("VLLM_IMAGE_MAX_SIDE", "1800")),
            data_dir=Path(os.getenv("HIVEBLOT_DATA_DIR", "data")).resolve(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
