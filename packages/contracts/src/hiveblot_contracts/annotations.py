"""Evaluation-case, prediction, review, annotation, and adjudication contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from .artifacts import ArtifactVisibility, BoundingRegion
from .base import ContractModel, Identifier
from .evaluation import ValidationIssue
from .identifiers import PipelineIdentifier, ProducerIdentifier
from .observation import ObservationState
from .structured_annotations import WesternBlotStructuredAnnotation


class ReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"
    NEEDS_ADJUDICATION = "needs_adjudication"
    ADJUDICATED = "adjudicated"


class CaseArtifactRole(StrEnum):
    SOURCE_DOCUMENT = "source_document"
    FIGURE = "figure"
    RAW_SOURCE = "raw_source"
    SUPPLEMENTARY = "supplementary"
    CONTEXT = "context"


class CaseSourceArtifact(ContractModel):
    artifact_id: UUID
    role: CaseArtifactRole
    page_number: int | None = Field(default=None, ge=1)


class EvaluationCaseRecord(ContractModel):
    case_id: UUID
    case_key: Identifier
    dataset_id: UUID | None = None
    assay_type: Literal["western_blot"] = "western_blot"
    visibility: ArtifactVisibility = ArtifactVisibility.PUBLIC
    organization_id: UUID | None = None
    review_status: ReviewStatus
    version: int = Field(ge=1)
    source_artifacts: tuple[CaseSourceArtifact, ...]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def visibility_and_timestamps_are_consistent(self) -> Self:
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public evaluation cases cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private evaluation cases require an organization")
        if self.updated_at < self.created_at:
            raise ValueError("evaluation case updated_at cannot precede created_at")
        return self


class PredictionEvidence(ContractModel):
    artifact_id: UUID
    field_path: str | None = Field(default=None, min_length=1, max_length=1000)
    region_id: UUID | None = None
    region: BoundingRegion | None = None
    description: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def evidence_must_locate_or_describe_source(self) -> Self:
        if (
            self.field_path is None
            and self.region_id is None
            and self.region is None
            and self.description is None
        ):
            raise ValueError("prediction evidence must include a field, region, or description")
        if self.region is not None and self.region.source_artifact_id != self.artifact_id:
            raise ValueError("prediction evidence region must use the evidence artifact")
        if (
            self.region is not None
            and self.region_id is not None
            and self.region.region_id != self.region_id
        ):
            raise ValueError("prediction evidence region IDs must agree")
        return self


class PredictionDocument(ContractModel):
    prediction_id: UUID
    case_id: UUID
    prediction_schema: Identifier
    prediction_schema_version: Identifier
    producer: ProducerIdentifier
    pipeline: PipelineIdentifier | None = None
    raw_output_json: str = Field(min_length=1)
    normalized_output_json: str | None = Field(default=None, min_length=1)
    configuration_json: str = Field(min_length=2)
    evidence: tuple[PredictionEvidence, ...] = ()
    validation_issues: tuple[ValidationIssue, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)
    trace_id: UUID
    latency_ms: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    created_at: AwareDatetime


class FieldPathTarget(ContractModel):
    target_type: Literal["field_path"] = "field_path"
    field_path: str = Field(pattern=r"^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])+)*$", max_length=1000)


class EntityFieldTarget(ContractModel):
    target_type: Literal["entity"] = "entity"
    entity_id: UUID
    field_name: Identifier | None = None


type FieldAnnotationTarget = Annotated[
    FieldPathTarget | EntityFieldTarget,
    Field(discriminator="target_type"),
]
type AnnotationScalar = StrictStr | StrictInt | StrictFloat | StrictBool


class FieldAnnotation(ContractModel):
    field_annotation_id: UUID
    target: FieldAnnotationTarget
    state: ObservationState
    value: AnnotationScalar | None = None
    original_extracted_text: str | None = Field(default=None, max_length=10_000)
    evidence_region_ids: tuple[UUID, ...] = ()
    notes: str | None = Field(default=None, max_length=10_000)

    @model_validator(mode="after")
    def absent_values_cannot_claim_a_scalar(self) -> Self:
        if (
            self.state in {ObservationState.ABSENT, ObservationState.NOT_APPLICABLE}
            and self.value is not None
        ):
            raise ValueError("absent and not-applicable annotations cannot include a value")
        return self


class SpatialAnnotationType(StrEnum):
    FIGURE = "figure"
    PANEL = "panel"
    BLOT = "blot"
    LANE = "lane"
    PROTEIN_ROW = "protein_row"
    BAND = "band"
    LABEL = "label"
    QUANTIFICATION_PLOT = "quantification_plot"


class SpatialAnnotation(ContractModel):
    spatial_annotation_id: UUID
    annotation_type: SpatialAnnotationType
    state: ObservationState
    region: BoundingRegion
    label: str | None = Field(default=None, max_length=1000)


class AnnotationRelationship(ContractModel):
    relationship_id: UUID
    subject_id: UUID
    relation_type: Identifier
    object_id: UUID

    @model_validator(mode="after")
    def relationship_cannot_self_reference(self) -> Self:
        if self.subject_id == self.object_id:
            raise ValueError("annotation relationship cannot self-reference")
        return self


class AnnotationRevision(ContractModel):
    revision_id: UUID
    annotation_id: UUID
    revision_number: int = Field(ge=1)
    prior_revision_id: UUID | None = None
    reviewer_id: UUID
    rationale: str | None = Field(default=None, max_length=10_000)
    error_codes: tuple[Identifier, ...] = ()
    field_annotations: tuple[FieldAnnotation, ...] = ()
    spatial_annotations: tuple[SpatialAnnotation, ...] = ()
    relationships: tuple[AnnotationRelationship, ...] = ()
    structured_annotation: WesternBlotStructuredAnnotation | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def snapshot_entity_ids_must_be_unique(self) -> Self:
        groups = (
            [item.field_annotation_id for item in self.field_annotations],
            [item.spatial_annotation_id for item in self.spatial_annotations],
            [item.relationship_id for item in self.relationships],
        )
        if any(len(values) != len(set(values)) for values in groups):
            raise ValueError("annotation snapshot IDs must be unique within each entity type")
        return self


class AnnotationDocumentRecord(ContractModel):
    annotation_id: UUID
    case_id: UUID
    reviewer_id: UUID
    visibility: ArtifactVisibility = ArtifactVisibility.PUBLIC
    organization_id: UUID | None = None
    head_revision_id: UUID
    revision_count: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def visibility_and_timestamps_are_consistent(self) -> Self:
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public annotations cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private annotations require an organization")
        if self.updated_at < self.created_at:
            raise ValueError("annotation updated_at cannot precede created_at")
        return self


class ReviewerAssignmentStatus(StrEnum):
    ASSIGNED = "assigned"
    RELEASED = "released"
    COMPLETED = "completed"


class ReviewerAssignment(ContractModel):
    assignment_id: UUID
    case_id: UUID
    reviewer_id: UUID
    exclusive: bool
    visibility: ArtifactVisibility = ArtifactVisibility.PUBLIC
    organization_id: UUID | None = None
    status: ReviewerAssignmentStatus
    version: int = Field(ge=1)
    assigned_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def assignment_scope_is_consistent(self) -> Self:
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public reviewer assignments cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private assignments require an organization")
        if self.updated_at < self.assigned_at:
            raise ValueError("assignment updated_at cannot precede assigned_at")
        return self


class AnnotationErrorCode(ContractModel):
    code: Identifier
    category: Identifier
    description: str = Field(min_length=1, max_length=4000)
    active: bool
    created_at: AwareDatetime


class AdjudicationRecord(ContractModel):
    adjudication_id: UUID
    case_id: UUID
    adjudicator_id: UUID
    visibility: ArtifactVisibility = ArtifactVisibility.PUBLIC
    organization_id: UUID | None = None
    selected_revision_id: UUID
    considered_revision_ids: tuple[UUID, ...] = Field(min_length=2)
    rationale: str = Field(min_length=1, max_length=10_000)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def selected_revision_must_be_considered(self) -> Self:
        if self.selected_revision_id not in self.considered_revision_ids:
            raise ValueError("selected revision must be included among considered revisions")
        if len(self.considered_revision_ids) != len(set(self.considered_revision_ids)):
            raise ValueError("considered revision IDs must be unique")
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public adjudications cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private adjudications require an organization")
        return self
