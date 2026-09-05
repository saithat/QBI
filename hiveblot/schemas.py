"""Public catalog response shapes. Internal filesystem paths stay in persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WesternBlotRecordResponse(PublicModel):
    id: int
    paper_id: str
    source_id: str | None = None
    page: int
    figure_label: str | None = None
    panel_label: str | None = None
    row_index: int | None = None
    lane_index: int | None = None
    target: str
    is_loading_control: bool
    western_blot_type: str
    sample: str | None = None
    organism: str | None = None
    treatment_context: str | None = None
    condition: str | None = None
    band_state: Literal["present", "absent", "uncertain"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_url: str | None = None
    doi: str | None = None
    image_sha256: str | None = None
    model_version: str | None = None
    updated_at: datetime


class RecordListResponse(PublicModel):
    results: tuple[WesternBlotRecordResponse, ...]
    count: int
    total: int
    limit: int
    offset: int


class RecordDetailResponse(PublicModel):
    record: WesternBlotRecordResponse
    image_url: str | None = None
    figure_caption: str
    paper_context: str
    warnings: tuple[str, ...]


class HealthResponse(PublicModel):
    status: Literal["ok", "not_ready"]
    database: Literal["ok", "unavailable"]
