"""HTTP-only source evidence workbench schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from hiveblot_contracts import ContractModel
from pydantic import BeforeValidator, Field

from .artifact_schemas import ArtifactRelationshipResponse
from .evaluation_schemas import BoundingRegionResponse


def _json_uuid(value: object) -> object:
    return UUID(value) if isinstance(value, str) else value


type JsonUUID = Annotated[UUID, BeforeValidator(_json_uuid)]


class PutSourceContextRequest(ContractModel):
    expected_head_revision_id: JsonUUID | None = None
    caption: str | None = Field(default=None, max_length=50_000)
    nearby_text: str | None = Field(default=None, max_length=100_000)


class SourceContextResponse(ContractModel):
    context_revision_id: UUID
    case_id: UUID
    artifact_id: UUID
    artifact_role: Literal["source_document", "figure", "raw_source", "supplementary", "context"]
    revision_number: int
    prior_revision_id: UUID | None
    caption: str | None
    nearby_text: str | None
    created_at: datetime


class SourceContextRevisionListResponse(ContractModel):
    case_id: UUID
    artifact_id: UUID
    artifact_role: Literal["source_document", "figure", "raw_source", "supplementary", "context"]
    revisions: tuple[SourceContextResponse, ...]


class WorkbenchArtifactResponse(ContractModel):
    artifact_id: UUID
    sha256: str
    media_type: str
    byte_size: int
    original_filename: str
    source_uri: str | None
    acquisition_method: Literal["user_upload", "source_adapter", "tool_output"]
    visibility: Literal["public", "organization_private"]
    organization_id: UUID | None
    relationships: tuple[ArtifactRelationshipResponse, ...]
    created_at: datetime


class WorkbenchEvidenceSourceResponse(ContractModel):
    artifact: WorkbenchArtifactResponse
    artifact_role: Literal["source_document", "figure", "raw_source", "supplementary", "context"]
    page_number: int | None
    caption: str | None
    nearby_text: str | None


class OverlayBaseResponse(ContractModel):
    overlay_id: UUID
    source_artifact_id: UUID
    region: BoundingRegionResponse
    label: str | None
    linked_field_keys: tuple[str, ...]


class PredictionOverlayResponse(OverlayBaseResponse):
    overlay_source: Literal["prediction"]
    prediction_id: UUID


class ReviewerOverlayResponse(OverlayBaseResponse):
    overlay_source: Literal["reviewer"]
    annotation_id: UUID
    revision_id: UUID
    reviewer_id: UUID


class AdjudicationOverlayResponse(OverlayBaseResponse):
    overlay_source: Literal["adjudication"]
    adjudication_id: UUID
    revision_id: UUID


type EvidenceOverlayResponse = Annotated[
    PredictionOverlayResponse | ReviewerOverlayResponse | AdjudicationOverlayResponse,
    Field(discriminator="overlay_source"),
]


class SourceEvidenceWorkbenchResponse(ContractModel):
    case_id: UUID
    case_version: int
    sources: tuple[WorkbenchEvidenceSourceResponse, ...]
    overlays: tuple[EvidenceOverlayResponse, ...]
