from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from .settings import Settings


class ModelUnavailable(RuntimeError):
    """Raised when the local vLLM service cannot satisfy a request."""


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str | None = None
    sample: str | None = None
    condition: str | None = None

    @field_validator("target", "sample", "condition", mode="before")
    @classmethod
    def empty_strings_are_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


SEARCH_FILTER_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "sample": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "condition": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["target", "sample", "condition"],
    "additionalProperties": False,
}

SEARCH_SYSTEM_PROMPT = """Extract filters from a natural-language western blot search.
Return a protein or gene as target, a cell line/tissue/sample as sample, and a
treatment/genotype/experimental condition as condition. Keep each value concise.
Use null when the query does not specify a field."""


def parse_filters_content(content: str) -> SearchFilters:
    try:
        return SearchFilters.model_validate_json(content)
    except ValueError as exc:
        raise ModelUnavailable("The local model returned invalid search filters") from exc


@dataclass
class LocalModelClient:
    settings: Settings
    transport: httpx.AsyncBaseTransport | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.vllm_api_key}"}

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(
                timeout=5,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    self.settings.vllm_base_url.removesuffix("/v1") + "/health"
                )
                return response.is_success
        except httpx.HTTPError:
            return False

    async def parse_search(self, query: str) -> SearchFilters:
        payload = {
            "model": self.settings.vllm_model,
            "messages": [
                {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            "temperature": 0,
            "max_tokens": 160,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "western_blot_search_filters",
                    "schema": SEARCH_FILTER_SCHEMA,
                },
            },
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.vllm_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.settings.vllm_base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            content = data["choices"][0]["message"]["content"]
            return parse_filters_content(content)
        except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ModelUnavailable("The local model service is unavailable") from exc
