"""HTTP-only request and response schemas, separate from worker and model contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from hiveblot_contracts import ContractModel
from pydantic import Field, field_validator


class SearchRequest(ContractModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator("query")
    @classmethod
    def query_must_have_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query cannot be blank")
        return value


class SearchFiltersResponse(ContractModel):
    target: str | None = None
    sample: str | None = None
    condition: str | None = None


class WesternBlotRecordResponse(ContractModel):
    id: int = Field(ge=1)
    paper_id: str
    page: int = Field(ge=1)
    figure_label: str | None = None
    panel_label: str | None = None
    target: str
    is_loading_control: bool
    western_blot_type: str
    sample: str | None = None
    organism: str | None = None
    treatment_context: str | None = None
    condition: str | None = None
    band_state: Literal["present", "absent", "uncertain"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    updated_at: datetime


class RecordListResponse(ContractModel):
    count: int = Field(ge=0)
    filters: SearchFiltersResponse
    results: tuple[WesternBlotRecordResponse, ...]


class SearchResponse(RecordListResponse):
    query: str


class RecordDetailResponse(ContractModel):
    record: WesternBlotRecordResponse
    image_url: str | None = None
    figure_caption: str
    paper_context: str
    warnings: tuple[str, ...]


class HealthResponse(ContractModel):
    status: Literal["ok", "not_ready"]
    database: Literal["ok", "unavailable"]
    model: Literal["ok", "unavailable"]
