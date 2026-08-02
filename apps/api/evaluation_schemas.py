"""HTTP-only schemas for evaluation, prediction, review, and adjudication APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from hiveblot_contracts import (
    AnnotationScalar,
    CaseArtifactRole,
    ContractModel,
    ObservationState,
    ReviewerAssignmentStatus,
    ReviewStatus,
    SpatialAnnotationType,
    ValidationSeverity,
)
from pydantic import BeforeValidator, Field, model_validator


def _json_uuid(value: object) -> object:
    return UUID(value) if isinstance(value, str) else value


def _json_tuple(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


type JsonUUID = Annotated[UUID, BeforeValidator(_json_uuid)]
type JsonTuple[T] = Annotated[tuple[T, ...], BeforeValidator(_json_tuple)]


class CaseSourceInput(ContractModel):
    artifact_id: JsonUUID
    role: Literal["source_document", "figure", "raw_source", "supplementary", "context"]
    page_number: int | None = Field(default=None, ge=1)


class CreateEvaluationCaseRequest(ContractModel):
    case_key: str = Field(min_length=1, max_length=200)
    dataset_id: JsonUUID | None = None
    source_artifacts: JsonTuple[CaseSourceInput] = Field(min_length=1)


class UpdateCaseStatusRequest(ContractModel):
    expected_version: int = Field(ge=1)
    review_status: Literal[
        "unreviewed", "in_review", "reviewed", "needs_adjudication", "adjudicated"
    ]


class CaseSourceResponse(ContractModel):
    artifact_id: UUID
    role: CaseArtifactRole
    page_number: int | None


class EvaluationCaseResponse(ContractModel):
    case_id: UUID
    case_key: str
    dataset_id: UUID | None
    assay_type: Literal["western_blot"]
    review_status: ReviewStatus
    version: int = Field(ge=1)
    source_artifacts: tuple[CaseSourceResponse, ...]
    created_at: datetime
    updated_at: datetime


class ModelProducerInput(ContractModel):
    kind: Literal["model"] = "model"
    provider: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)


class ToolProducerInput(ContractModel):
    kind: Literal["tool"] = "tool"
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)


type ProducerInput = Annotated[
    ModelProducerInput | ToolProducerInput,
    Field(discriminator="kind"),
]


class PipelineInput(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)


class PredictionEvidenceInput(ContractModel):
    artifact_id: JsonUUID
    field_path: str | None = Field(default=None, min_length=1, max_length=1000)
    region_id: JsonUUID | None = None
    description: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def evidence_must_locate_or_describe_source(self) -> Self:
        if self.field_path is None and self.region_id is None and self.description is None:
            raise ValueError("prediction evidence must include a field, region, or description")
        return self


class ValidationIssueInput(ContractModel):
    issue_id: JsonUUID
    severity: Literal["info", "warning", "error"]
    code: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    field_path: str | None = Field(default=None, min_length=1, max_length=1000)
    evidence_artifact_ids: JsonTuple[JsonUUID] = ()


class CreatePredictionRequest(ContractModel):
    prediction_schema: str = Field(min_length=1, max_length=200)
    prediction_schema_version: str = Field(min_length=1, max_length=200)
    producer: ProducerInput
    pipeline: PipelineInput | None = None
    raw_output_json: str = Field(min_length=1)
    normalized_output_json: str | None = Field(default=None, min_length=1)
    configuration_json: str = Field(min_length=2)
    evidence: JsonTuple[PredictionEvidenceInput] = ()
    validation_issues: JsonTuple[ValidationIssueInput] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)
    trace_id: JsonUUID
    latency_ms: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)


class ProducerResponse(ContractModel):
    kind: Literal["model", "tool"]
    name: str
    version: str
    provider: str | None = None


class PipelineResponse(ContractModel):
    name: str
    version: str


class PredictionEvidenceResponse(ContractModel):
    artifact_id: UUID
    field_path: str | None
    region_id: UUID | None
    description: str | None


class ValidationIssueResponse(ContractModel):
    issue_id: UUID
    severity: ValidationSeverity
    code: str
    message: str
    field_path: str | None
    evidence_artifact_ids: tuple[UUID, ...]


class PredictionResponse(ContractModel):
    prediction_id: UUID
    case_id: UUID
    prediction_schema: str
    prediction_schema_version: str
    producer: ProducerResponse
    pipeline: PipelineResponse | None
    raw_output_json: str
    normalized_output_json: str | None
    configuration_json: str
    evidence: tuple[PredictionEvidenceResponse, ...]
    validation_issues: tuple[ValidationIssueResponse, ...]
    confidence: float | None
    trace_id: UUID
    latency_ms: int
    cost_microusd: int
    created_at: datetime


class PredictionListResponse(ContractModel):
    case_id: UUID
    predictions: tuple[PredictionResponse, ...]


class FieldPathTargetInput(ContractModel):
    target_type: Literal["field_path"] = "field_path"
    field_path: str = Field(pattern=r"^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])+)*$", max_length=1000)


class EntityFieldTargetInput(ContractModel):
    target_type: Literal["entity"] = "entity"
    entity_id: JsonUUID
    field_name: str | None = Field(default=None, min_length=1, max_length=200)


type FieldTargetInput = Annotated[
    FieldPathTargetInput | EntityFieldTargetInput,
    Field(discriminator="target_type"),
]


class FieldAnnotationInput(ContractModel):
    field_annotation_id: JsonUUID
    target: FieldTargetInput
    state: Literal["present", "absent", "unknown", "ambiguous", "not_applicable"]
    value: AnnotationScalar | None = None
    original_extracted_text: str | None = Field(default=None, max_length=10_000)
    evidence_region_ids: JsonTuple[JsonUUID] = ()
    notes: str | None = Field(default=None, max_length=10_000)

    @model_validator(mode="after")
    def absent_values_cannot_claim_a_scalar(self) -> Self:
        if self.state in {"absent", "not_applicable"} and self.value is not None:
            raise ValueError("absent and not-applicable annotations cannot include a value")
        return self


class BoundingRegionInput(ContractModel):
    region_id: JsonUUID
    source_artifact_id: JsonUUID
    coordinate_space: Literal["source_pixels"] = "source_pixels"
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    canvas_width: int = Field(gt=0)
    canvas_height: int = Field(gt=0)
    page_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def region_must_fit_source_canvas(self) -> Self:
        if self.x + self.width > self.canvas_width:
            raise ValueError("region exceeds source canvas width")
        if self.y + self.height > self.canvas_height:
            raise ValueError("region exceeds source canvas height")
        return self


class SpatialAnnotationInput(ContractModel):
    spatial_annotation_id: JsonUUID
    annotation_type: Literal[
        "figure", "panel", "blot", "lane", "protein_row", "band", "label", "quantification_plot"
    ]
    state: Literal["present", "absent", "unknown", "ambiguous", "not_applicable"]
    region: BoundingRegionInput
    label: str | None = Field(default=None, max_length=1000)


class AnnotationRelationshipInput(ContractModel):
    relationship_id: JsonUUID
    subject_id: JsonUUID
    relation_type: str = Field(min_length=1, max_length=200)
    object_id: JsonUUID

    @model_validator(mode="after")
    def relationship_cannot_self_reference(self) -> Self:
        if self.subject_id == self.object_id:
            raise ValueError("annotation relationship cannot self-reference")
        return self


class AnnotationSnapshotInput(ContractModel):
    rationale: str | None = Field(default=None, max_length=10_000)
    error_codes: JsonTuple[str] = ()
    field_annotations: JsonTuple[FieldAnnotationInput] = ()
    spatial_annotations: JsonTuple[SpatialAnnotationInput] = ()
    relationships: JsonTuple[AnnotationRelationshipInput] = ()

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


class CreateAnnotationRequest(AnnotationSnapshotInput):
    reviewer_id: JsonUUID


class AppendAnnotationRevisionRequest(AnnotationSnapshotInput):
    expected_head_revision_id: JsonUUID
    reviewer_id: JsonUUID


class FieldPathTargetResponse(ContractModel):
    target_type: Literal["field_path"]
    field_path: str


class EntityFieldTargetResponse(ContractModel):
    target_type: Literal["entity"]
    entity_id: UUID
    field_name: str | None


type FieldTargetResponse = Annotated[
    FieldPathTargetResponse | EntityFieldTargetResponse,
    Field(discriminator="target_type"),
]


class FieldAnnotationResponse(ContractModel):
    field_annotation_id: UUID
    target: FieldTargetResponse
    state: ObservationState
    value: AnnotationScalar | None
    original_extracted_text: str | None
    evidence_region_ids: tuple[UUID, ...]
    notes: str | None


class BoundingRegionResponse(ContractModel):
    region_id: UUID
    source_artifact_id: UUID
    coordinate_space: Literal["source_pixels"]
    x: float
    y: float
    width: float
    height: float
    canvas_width: int
    canvas_height: int
    page_number: int | None


class SpatialAnnotationResponse(ContractModel):
    spatial_annotation_id: UUID
    annotation_type: SpatialAnnotationType
    state: ObservationState
    region: BoundingRegionResponse
    label: str | None


class AnnotationRelationshipResponse(ContractModel):
    relationship_id: UUID
    subject_id: UUID
    relation_type: str
    object_id: UUID


class AnnotationRevisionResponse(ContractModel):
    revision_id: UUID
    annotation_id: UUID
    revision_number: int
    prior_revision_id: UUID | None
    reviewer_id: UUID
    rationale: str | None
    error_codes: tuple[str, ...]
    field_annotations: tuple[FieldAnnotationResponse, ...]
    spatial_annotations: tuple[SpatialAnnotationResponse, ...]
    relationships: tuple[AnnotationRelationshipResponse, ...]
    created_at: datetime


class AnnotationDocumentResponse(ContractModel):
    annotation_id: UUID
    case_id: UUID
    reviewer_id: UUID
    head_revision_id: UUID
    revision_count: int
    created_at: datetime
    updated_at: datetime


class AnnotationMutationResponse(ContractModel):
    annotation: AnnotationDocumentResponse
    revision: AnnotationRevisionResponse


class AnnotationListResponse(ContractModel):
    case_id: UUID
    annotations: tuple[AnnotationDocumentResponse, ...]


class RevisionListResponse(ContractModel):
    annotation_id: UUID
    revisions: tuple[AnnotationRevisionResponse, ...]


class CreateAssignmentRequest(ContractModel):
    reviewer_id: JsonUUID
    exclusive: bool = False


class UpdateAssignmentRequest(ContractModel):
    expected_version: int = Field(ge=1)
    status: Literal["released", "completed"]


class AssignmentResponse(ContractModel):
    assignment_id: UUID
    case_id: UUID
    reviewer_id: UUID
    exclusive: bool
    status: ReviewerAssignmentStatus
    version: int
    assigned_at: datetime
    updated_at: datetime


class AssignmentListResponse(ContractModel):
    case_id: UUID
    assignments: tuple[AssignmentResponse, ...]


class CreateErrorCodeRequest(ContractModel):
    code: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)


class ErrorCodeResponse(ContractModel):
    code: str
    category: str
    description: str
    active: bool
    created_at: datetime


class ErrorCodeListResponse(ContractModel):
    error_codes: tuple[ErrorCodeResponse, ...]


class CreateAdjudicationRequest(ContractModel):
    adjudicator_id: JsonUUID
    selected_revision_id: JsonUUID
    considered_revision_ids: JsonTuple[JsonUUID] = Field(min_length=2)
    rationale: str = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def selected_revision_must_be_considered(self) -> Self:
        if self.selected_revision_id not in self.considered_revision_ids:
            raise ValueError("selected revision must be included among considered revisions")
        if len(self.considered_revision_ids) != len(set(self.considered_revision_ids)):
            raise ValueError("considered revision IDs must be unique")
        return self


class AdjudicationResponse(ContractModel):
    adjudication_id: UUID
    case_id: UUID
    adjudicator_id: UUID
    selected_revision_id: UUID
    considered_revision_ids: tuple[UUID, ...]
    rationale: str
    created_at: datetime


class AdjudicationListResponse(ContractModel):
    case_id: UUID
    adjudications: tuple[AdjudicationResponse, ...]
