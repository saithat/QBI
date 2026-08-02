"""HTTP-only schemas for stored-artifact western-blot extraction."""

from __future__ import annotations

from uuid import UUID

from hiveblot_contracts import ContractModel
from pydantic import Field, StrictFloat, StrictInt

from .evaluation_schemas import JsonUUID


class StartWesternBlotExtractionRequest(ContractModel):
    source_artifact_id: JsonUUID
    implementation_name: str = Field(default="legacy-local-vlm", min_length=1, max_length=200)
    dpi: StrictInt = Field(default=350, ge=72, le=1200)
    minimum_candidate_score: StrictFloat = Field(default=0.35, ge=0, le=1)
    minimum_model_score: StrictFloat = Field(default=0.65, ge=0, le=1)
    maximum_candidates: StrictInt = Field(default=50, ge=1, le=500)
    image_max_side: StrictInt = Field(default=1800, ge=128, le=16384)
    model_max_tokens: StrictInt = Field(default=4096, ge=1, le=65536)
    trace_id: JsonUUID | None = None


class ReplayWesternBlotComponentRequest(ContractModel):
    trace_id: JsonUUID | None = None


class WesternBlotImplementationResponse(ContractModel):
    implementation_name: str
    implementation_version: str
    pipeline_name: str
    pipeline_version: str
    detector_name: str
    detector_version: str
    model_provider: str
    model_name: str
    model_version: str
    prompt_version: str
    assembler_name: str
    assembler_version: str


class WesternBlotImplementationListResponse(ContractModel):
    implementations: tuple[WesternBlotImplementationResponse, ...]


class WesternBlotExtractionRunResponse(ContractModel):
    case_id: UUID
    run_id: UUID
    definition_id: UUID
    detector_invocation_id: UUID
    model_invocation_id: UUID
    assembler_invocation_id: UUID
    prediction_id: UUID
    publication_id: UUID
    trace_id: UUID
    pipeline_name: str
    pipeline_version: str
    source_artifact_id: UUID
    source_sha256: str
    candidate_count: int
    relevant_candidate_count: int
    panel_count: int
    protein_count: int
    lane_count: int
    region_count: int
    warning_count: int
    confidence: float | None


class WesternBlotComponentReplayResponse(ContractModel):
    run_id: UUID
    case_id: UUID
    invocation_id: UUID
    replay_of_invocation_id: UUID
    component_key: str = Field(pattern=r"^(figure_detection|western_blot_vision|case_assembly)$")
    trace_id: UUID
    prediction_id: UUID | None
    publication_id: UUID | None
