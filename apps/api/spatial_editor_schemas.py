"""HTTP contracts for the source-pixel spatial annotation editor."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from hiveblot_contracts import (
    ContractModel,
    SpatialAnnotationComparison,
    SpatialAnnotationSet,
)
from pydantic import BeforeValidator, Field, model_validator

from .evaluation_schemas import (
    AnnotationDocumentResponse,
    AnnotationRevisionResponse,
    ErrorCodeResponse,
    JsonTuple,
    JsonUUID,
)


def _json_spatial_set(value: object) -> object:
    if isinstance(value, SpatialAnnotationSet):
        return value
    return SpatialAnnotationSet.model_validate_json(json.dumps(value))


type JsonSpatialAnnotationSet = Annotated[
    SpatialAnnotationSet,
    BeforeValidator(_json_spatial_set),
]


class SpatialPredictionOption(ContractModel):
    prediction_id: UUID
    prediction_schema_version: str
    producer_name: str
    producer_version: str
    confidence: float | None
    created_at: datetime
    spatial_output_available: bool


class SpatialRevisionSummary(ContractModel):
    revision_id: UUID
    revision_number: int = Field(ge=1)
    prior_revision_id: UUID | None
    rationale: str | None
    error_codes: tuple[str, ...]
    created_at: datetime
    spatial_annotation_count: int = Field(ge=0)


class SpatialEditorResponse(ContractModel):
    case_id: UUID
    reviewer_id: UUID
    annotation_document: AnnotationDocumentResponse | None
    head_revision: AnnotationRevisionResponse | None
    revisions: tuple[SpatialRevisionSummary, ...]
    predictions: tuple[SpatialPredictionOption, ...]
    selected_prediction_id: UUID | None
    prediction_set: SpatialAnnotationSet | None
    draft_set: SpatialAnnotationSet
    comparison: SpatialAnnotationComparison | None
    error_codes: tuple[ErrorCodeResponse, ...]


class SaveSpatialAnnotationRequest(ContractModel):
    reviewer_id: JsonUUID
    expected_head_revision_id: JsonUUID | None = None
    annotation_set: JsonSpatialAnnotationSet
    rationale: str | None = Field(default=None, max_length=10_000)
    error_codes: JsonTuple[str] = ()


class AcceptSpatialPredictionRequest(ContractModel):
    prediction_id: JsonUUID
    base_set: JsonSpatialAnnotationSet | None = None
    accepted_region_ids: JsonTuple[JsonUUID] = ()
    accept_all: bool = False

    @model_validator(mode="after")
    def an_acceptance_scope_is_required(self) -> Self:
        if self.accept_all and self.accepted_region_ids:
            raise ValueError("accept_all and accepted_region_ids are mutually exclusive")
        if not self.accept_all and not self.accepted_region_ids:
            raise ValueError("accept_all or accepted_region_ids is required")
        if len(self.accepted_region_ids) != len(set(self.accepted_region_ids)):
            raise ValueError("accepted region IDs must be unique")
        return self


class SpatialAnnotationDraftResponse(ContractModel):
    annotation_set: SpatialAnnotationSet
    comparison: SpatialAnnotationComparison


class UndoSpatialAnnotationRequest(ContractModel):
    reviewer_id: JsonUUID
    expected_head_revision_id: JsonUUID
    target_revision_id: JsonUUID
    rationale: str | None = Field(default=None, max_length=10_000)
