"""HTTP contracts for the typed western-blot annotation editor."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from hiveblot_contracts import (
    CanonicalEntityReference,
    CanonicalEntityType,
    ContractModel,
    StructuredAnnotationComparison,
    WesternBlotStructuredAnnotation,
)
from pydantic import Field, model_validator

from .evaluation_schemas import (
    AnnotationDocumentResponse,
    AnnotationRevisionResponse,
    ErrorCodeResponse,
    JsonStructuredAnnotation,
    JsonTuple,
    JsonUUID,
)


class PredictionEditorOption(ContractModel):
    prediction_id: UUID
    prediction_schema_version: str
    producer_name: str
    producer_version: str
    confidence: float | None
    created_at: datetime
    structured_output_available: bool


class RevisionEditorSummary(ContractModel):
    revision_id: UUID
    revision_number: int = Field(ge=1)
    prior_revision_id: UUID | None
    rationale: str | None
    error_codes: tuple[str, ...]
    created_at: datetime
    has_structured_annotation: bool


class StructuredEditorResponse(ContractModel):
    case_id: UUID
    reviewer_id: UUID
    annotation_document: AnnotationDocumentResponse | None
    head_revision: AnnotationRevisionResponse | None
    revisions: tuple[RevisionEditorSummary, ...]
    predictions: tuple[PredictionEditorOption, ...]
    selected_prediction_id: UUID | None
    prediction_annotation: WesternBlotStructuredAnnotation | None
    draft_annotation: WesternBlotStructuredAnnotation
    comparison: StructuredAnnotationComparison | None
    error_codes: tuple[ErrorCodeResponse, ...]


class SaveStructuredAnnotationRequest(ContractModel):
    reviewer_id: JsonUUID
    expected_head_revision_id: JsonUUID | None = None
    visibility: Literal["public", "organization_private"] | None = None
    organization_id: JsonUUID | None = None
    annotation: JsonStructuredAnnotation
    rationale: str | None = Field(default=None, max_length=10_000)
    error_codes: JsonTuple[str] = ()

    @model_validator(mode="after")
    def explicit_scope_is_complete(self) -> Self:
        if self.visibility == "public" and self.organization_id is not None:
            raise ValueError("public annotations cannot include organization_id")
        if self.visibility == "organization_private" and self.organization_id is None:
            raise ValueError("organization-private annotations require organization_id")
        if self.visibility is None and self.organization_id is not None:
            raise ValueError("organization_id requires an explicit visibility")
        return self


class AcceptPredictionRequest(ContractModel):
    prediction_id: JsonUUID
    base_annotation: JsonStructuredAnnotation | None = None
    accepted_entity_ids: JsonTuple[JsonUUID] = ()
    accept_all: bool = False

    @model_validator(mode="after")
    def an_acceptance_scope_is_required(self) -> Self:
        if self.accept_all and self.accepted_entity_ids:
            raise ValueError("accept_all and accepted_entity_ids are mutually exclusive")
        if not self.accept_all and not self.accepted_entity_ids:
            raise ValueError("accept_all or accepted_entity_ids is required")
        if len(self.accepted_entity_ids) != len(set(self.accepted_entity_ids)):
            raise ValueError("accepted entity IDs must be unique")
        return self


class StructuredAnnotationDraftResponse(ContractModel):
    annotation: WesternBlotStructuredAnnotation
    comparison: StructuredAnnotationComparison


class UndoStructuredAnnotationRequest(ContractModel):
    reviewer_id: JsonUUID
    expected_head_revision_id: JsonUUID
    target_revision_id: JsonUUID
    rationale: str | None = Field(default=None, max_length=10_000)


class CanonicalEntityListResponse(ContractModel):
    query: str
    entity_type: CanonicalEntityType | None
    candidates: tuple[CanonicalEntityReference, ...]
