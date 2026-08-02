"""Typed persistence records; these are deliberately not API or queue contracts."""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict


class WesternBlotRecordWrite(TypedDict):
    paper_id: str
    candidate_key: str
    source_pdf: str | None
    candidate_path: str | None
    page: int
    figure_label: str | None
    panel_label: str | None
    row_index: int | None
    lane_index: int | None
    target: str
    is_loading_control: bool
    western_blot_type: str
    sample: str | None
    organism: str | None
    treatment_context: str | None
    condition: str | None
    band_state: str
    confidence: float | None


class WesternBlotRecordListRow(TypedDict):
    id: int
    paper_id: str
    page: int
    figure_label: str | None
    panel_label: str | None
    target: str
    is_loading_control: bool
    western_blot_type: str
    sample: str | None
    organism: str | None
    treatment_context: str | None
    condition: str | None
    band_state: str
    confidence: float | None
    updated_at: datetime


class WesternBlotRecordDetailRow(WesternBlotRecordListRow):
    source_pdf: str | None
    candidate_path: str | None
    row_index: int | None
    lane_index: int | None
