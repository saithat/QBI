"""Strict evaluation-case, prediction, annotation, and adjudication API."""

from __future__ import annotations

from typing import Annotated, NoReturn, TypedDict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from hiveblot_contracts import (
    AdjudicationRecord,
    AnnotationDocumentRecord,
    AnnotationErrorCode,
    AnnotationRelationship,
    AnnotationRevision,
    BoundingRegion,
    CaseArtifactRole,
    CaseSourceArtifact,
    EntityFieldTarget,
    EvaluationCaseRecord,
    FieldAnnotation,
    FieldPathTarget,
    ModelIdentifier,
    ObservationState,
    PipelineIdentifier,
    PredictionDocument,
    PredictionEvidence,
    ReviewerAssignment,
    ReviewerAssignmentStatus,
    ReviewStatus,
    SpatialAnnotation,
    SpatialAnnotationType,
    ToolIdentifier,
    ValidationIssue,
    ValidationSeverity,
    WesternBlotStructuredAnnotation,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationNotFound,
    EvaluationService,
    InvalidEvaluationState,
)

from .evaluation_dependencies import get_evaluation_service
from .evaluation_schemas import (
    AdjudicationListResponse,
    AdjudicationResponse,
    AnnotationDocumentResponse,
    AnnotationListResponse,
    AnnotationMutationResponse,
    AnnotationRelationshipInput,
    AnnotationRevisionResponse,
    AnnotationSnapshotInput,
    AppendAnnotationRevisionRequest,
    AssignmentListResponse,
    AssignmentResponse,
    BoundingRegionInput,
    CreateAdjudicationRequest,
    CreateAnnotationRequest,
    CreateAssignmentRequest,
    CreateErrorCodeRequest,
    CreateEvaluationCaseRequest,
    CreatePredictionRequest,
    EntityFieldTargetInput,
    ErrorCodeListResponse,
    ErrorCodeResponse,
    EvaluationCaseResponse,
    FieldAnnotationInput,
    ModelProducerInput,
    PredictionBoundingRegionInput,
    PredictionListResponse,
    PredictionResponse,
    RevisionListResponse,
    SpatialAnnotationInput,
    UpdateAssignmentRequest,
    UpdateCaseStatusRequest,
)

router = APIRouter(prefix="/api/v1", tags=["evaluation"])
EvaluationServiceDependency = Annotated[EvaluationService, Depends(get_evaluation_service)]


class SnapshotArguments(TypedDict):
    rationale: str | None
    error_codes: tuple[str, ...]
    field_annotations: tuple[FieldAnnotation, ...]
    spatial_annotations: tuple[SpatialAnnotation, ...]
    relationships: tuple[AnnotationRelationship, ...]
    structured_annotation: WesternBlotStructuredAnnotation | None


@router.post(
    "/evaluation-cases",
    response_model=EvaluationCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_case(
    request: CreateEvaluationCaseRequest,
    service: EvaluationServiceDependency,
) -> EvaluationCaseResponse:
    try:
        record = service.create_case(
            case_key=request.case_key,
            dataset_id=request.dataset_id,
            source_artifacts=tuple(
                CaseSourceArtifact(
                    artifact_id=source.artifact_id,
                    role=CaseArtifactRole(source.role),
                    page_number=source.page_number,
                )
                for source in request.source_artifacts
            ),
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return _case_response(record)


@router.get("/evaluation-cases/{case_id}", response_model=EvaluationCaseResponse)
def get_case(case_id: UUID, service: EvaluationServiceDependency) -> EvaluationCaseResponse:
    try:
        return _case_response(service.get_case(case_id))
    except EvaluationError as exc:
        _raise_http(exc)


@router.patch("/evaluation-cases/{case_id}/status", response_model=EvaluationCaseResponse)
def update_case_status(
    case_id: UUID,
    request: UpdateCaseStatusRequest,
    service: EvaluationServiceDependency,
) -> EvaluationCaseResponse:
    try:
        record = service.update_case_status(
            case_id,
            expected_version=request.expected_version,
            review_status=ReviewStatus(request.review_status),
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return _case_response(record)


@router.post(
    "/evaluation-cases/{case_id}/predictions",
    response_model=PredictionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_prediction(
    case_id: UUID,
    request: CreatePredictionRequest,
    service: EvaluationServiceDependency,
) -> PredictionResponse:
    producer = (
        ModelIdentifier(
            provider=request.producer.provider,
            name=request.producer.name,
            version=request.producer.version,
        )
        if isinstance(request.producer, ModelProducerInput)
        else ToolIdentifier(name=request.producer.name, version=request.producer.version)
    )
    pipeline = (
        PipelineIdentifier(name=request.pipeline.name, version=request.pipeline.version)
        if request.pipeline is not None
        else None
    )
    try:
        document = service.add_prediction(
            case_id,
            prediction_schema=request.prediction_schema,
            prediction_schema_version=request.prediction_schema_version,
            producer=producer,
            pipeline=pipeline,
            raw_output_json=request.raw_output_json,
            normalized_output_json=request.normalized_output_json,
            configuration_json=request.configuration_json,
            evidence=tuple(
                PredictionEvidence(
                    artifact_id=item.artifact_id,
                    field_path=item.field_path,
                    region_id=item.region_id,
                    region=_bounding_region(item.region) if item.region is not None else None,
                    description=item.description,
                )
                for item in request.evidence
            ),
            validation_issues=tuple(
                ValidationIssue(
                    issue_id=item.issue_id,
                    severity=ValidationSeverity(item.severity),
                    code=item.code,
                    message=item.message,
                    field_path=item.field_path,
                    evidence_artifact_ids=item.evidence_artifact_ids,
                )
                for item in request.validation_issues
            ),
            confidence=request.confidence,
            trace_id=request.trace_id,
            latency_ms=request.latency_ms,
            cost_microusd=request.cost_microusd,
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return _prediction_response(document)


@router.get(
    "/evaluation-cases/{case_id}/predictions",
    response_model=PredictionListResponse,
)
def list_predictions(
    case_id: UUID,
    service: EvaluationServiceDependency,
) -> PredictionListResponse:
    try:
        documents = service.list_predictions(case_id)
    except EvaluationError as exc:
        _raise_http(exc)
    return PredictionListResponse(
        case_id=case_id,
        predictions=tuple(_prediction_response(document) for document in documents),
    )


@router.get("/predictions/{prediction_id}", response_model=PredictionResponse)
def get_prediction(
    prediction_id: UUID,
    service: EvaluationServiceDependency,
) -> PredictionResponse:
    try:
        return _prediction_response(service.get_prediction(prediction_id))
    except EvaluationError as exc:
        _raise_http(exc)


@router.post(
    "/evaluation-cases/{case_id}/annotations",
    response_model=AnnotationMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_annotation(
    case_id: UUID,
    request: CreateAnnotationRequest,
    service: EvaluationServiceDependency,
) -> AnnotationMutationResponse:
    try:
        document, revision = service.create_annotation(
            case_id,
            reviewer_id=request.reviewer_id,
            **_snapshot_arguments(request),
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return AnnotationMutationResponse(
        annotation=_annotation_response(document),
        revision=_revision_response(revision),
    )


@router.get(
    "/evaluation-cases/{case_id}/annotations",
    response_model=AnnotationListResponse,
)
def list_annotations(
    case_id: UUID,
    service: EvaluationServiceDependency,
) -> AnnotationListResponse:
    try:
        documents = service.list_annotations(case_id)
    except EvaluationError as exc:
        _raise_http(exc)
    return AnnotationListResponse(
        case_id=case_id,
        annotations=tuple(_annotation_response(document) for document in documents),
    )


@router.get("/annotations/{annotation_id}", response_model=AnnotationDocumentResponse)
def get_annotation(
    annotation_id: UUID,
    service: EvaluationServiceDependency,
) -> AnnotationDocumentResponse:
    try:
        return _annotation_response(service.get_annotation(annotation_id))
    except EvaluationError as exc:
        _raise_http(exc)


@router.post(
    "/annotations/{annotation_id}/revisions",
    response_model=AnnotationMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def append_revision(
    annotation_id: UUID,
    request: AppendAnnotationRevisionRequest,
    service: EvaluationServiceDependency,
) -> AnnotationMutationResponse:
    try:
        document, revision = service.append_revision(
            annotation_id,
            expected_head_revision_id=request.expected_head_revision_id,
            reviewer_id=request.reviewer_id,
            **_snapshot_arguments(request),
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return AnnotationMutationResponse(
        annotation=_annotation_response(document),
        revision=_revision_response(revision),
    )


@router.get(
    "/annotations/{annotation_id}/revisions",
    response_model=RevisionListResponse,
)
def list_revisions(
    annotation_id: UUID,
    service: EvaluationServiceDependency,
) -> RevisionListResponse:
    try:
        revisions = service.list_revisions(annotation_id)
    except EvaluationError as exc:
        _raise_http(exc)
    return RevisionListResponse(
        annotation_id=annotation_id,
        revisions=tuple(_revision_response(revision) for revision in revisions),
    )


@router.get("/annotation-revisions/{revision_id}", response_model=AnnotationRevisionResponse)
def get_revision(
    revision_id: UUID,
    service: EvaluationServiceDependency,
) -> AnnotationRevisionResponse:
    try:
        return _revision_response(service.get_revision(revision_id))
    except EvaluationError as exc:
        _raise_http(exc)


@router.post(
    "/evaluation-cases/{case_id}/assignments",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_assignment(
    case_id: UUID,
    request: CreateAssignmentRequest,
    service: EvaluationServiceDependency,
) -> AssignmentResponse:
    try:
        assignment = service.assign_reviewer(
            case_id,
            reviewer_id=request.reviewer_id,
            exclusive=request.exclusive,
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return _assignment_response(assignment)


@router.get(
    "/evaluation-cases/{case_id}/assignments",
    response_model=AssignmentListResponse,
)
def list_assignments(
    case_id: UUID,
    service: EvaluationServiceDependency,
) -> AssignmentListResponse:
    try:
        assignments = service.list_assignments(case_id)
    except EvaluationError as exc:
        _raise_http(exc)
    return AssignmentListResponse(
        case_id=case_id,
        assignments=tuple(_assignment_response(item) for item in assignments),
    )


@router.patch("/assignments/{assignment_id}", response_model=AssignmentResponse)
def update_assignment(
    assignment_id: UUID,
    request: UpdateAssignmentRequest,
    service: EvaluationServiceDependency,
) -> AssignmentResponse:
    try:
        assignment = service.update_assignment(
            assignment_id,
            expected_version=request.expected_version,
            status=ReviewerAssignmentStatus(request.status),
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return _assignment_response(assignment)


@router.get("/annotation-error-codes", response_model=ErrorCodeListResponse)
def list_error_codes(service: EvaluationServiceDependency) -> ErrorCodeListResponse:
    return ErrorCodeListResponse(
        error_codes=tuple(_error_code_response(item) for item in service.list_error_codes()),
    )


@router.post(
    "/annotation-error-codes",
    response_model=ErrorCodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_error_code(
    request: CreateErrorCodeRequest,
    service: EvaluationServiceDependency,
) -> ErrorCodeResponse:
    try:
        record = service.add_error_code(
            code=request.code,
            category=request.category,
            description=request.description,
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return _error_code_response(record)


@router.post(
    "/evaluation-cases/{case_id}/adjudications",
    response_model=AdjudicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_adjudication(
    case_id: UUID,
    request: CreateAdjudicationRequest,
    service: EvaluationServiceDependency,
) -> AdjudicationResponse:
    try:
        record = service.adjudicate(
            case_id,
            adjudicator_id=request.adjudicator_id,
            selected_revision_id=request.selected_revision_id,
            considered_revision_ids=request.considered_revision_ids,
            rationale=request.rationale,
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return _adjudication_response(record)


@router.get(
    "/evaluation-cases/{case_id}/adjudications",
    response_model=AdjudicationListResponse,
)
def list_adjudications(
    case_id: UUID,
    service: EvaluationServiceDependency,
) -> AdjudicationListResponse:
    try:
        records = service.list_adjudications(case_id)
    except EvaluationError as exc:
        _raise_http(exc)
    return AdjudicationListResponse(
        case_id=case_id,
        adjudications=tuple(_adjudication_response(item) for item in records),
    )


def _snapshot_arguments(request: AnnotationSnapshotInput) -> SnapshotArguments:
    return {
        "rationale": request.rationale,
        "error_codes": request.error_codes,
        "field_annotations": tuple(_field_annotation(item) for item in request.field_annotations),
        "spatial_annotations": tuple(
            _spatial_annotation(item) for item in request.spatial_annotations
        ),
        "relationships": tuple(_relationship(item) for item in request.relationships),
        "structured_annotation": request.structured_annotation,
    }


def _field_annotation(value: FieldAnnotationInput) -> FieldAnnotation:
    target = (
        EntityFieldTarget(
            entity_id=value.target.entity_id,
            field_name=value.target.field_name,
        )
        if isinstance(value.target, EntityFieldTargetInput)
        else FieldPathTarget(field_path=value.target.field_path)
    )
    return FieldAnnotation(
        field_annotation_id=value.field_annotation_id,
        target=target,
        state=ObservationState(value.state),
        value=value.value,
        original_extracted_text=value.original_extracted_text,
        evidence_region_ids=value.evidence_region_ids,
        notes=value.notes,
    )


def _spatial_annotation(value: SpatialAnnotationInput) -> SpatialAnnotation:
    return SpatialAnnotation(
        spatial_annotation_id=value.spatial_annotation_id,
        annotation_type=SpatialAnnotationType(value.annotation_type),
        state=ObservationState(value.state),
        region=_bounding_region(value.region),
        label=value.label,
    )


def _bounding_region(
    value: BoundingRegionInput | PredictionBoundingRegionInput,
) -> BoundingRegion:
    return BoundingRegion(
        region_id=value.region_id,
        source_artifact_id=value.source_artifact_id,
        x=value.x,
        y=value.y,
        width=value.width,
        height=value.height,
        canvas_width=value.canvas_width,
        canvas_height=value.canvas_height,
        page_number=value.page_number,
    )


def _relationship(value: AnnotationRelationshipInput) -> AnnotationRelationship:
    return AnnotationRelationship(
        relationship_id=value.relationship_id,
        subject_id=value.subject_id,
        relation_type=value.relation_type,
        object_id=value.object_id,
    )


def _case_response(record: EvaluationCaseRecord) -> EvaluationCaseResponse:
    return EvaluationCaseResponse.model_validate(record.model_dump(mode="python"))


def _prediction_response(record: PredictionDocument) -> PredictionResponse:
    payload = record.model_dump(mode="python")
    producer = payload["producer"]
    if producer.get("kind") == "tool":
        producer["provider"] = None
    return PredictionResponse.model_validate(payload)


def _annotation_response(record: AnnotationDocumentRecord) -> AnnotationDocumentResponse:
    return AnnotationDocumentResponse.model_validate(record.model_dump(mode="python"))


def _revision_response(record: AnnotationRevision) -> AnnotationRevisionResponse:
    return AnnotationRevisionResponse.model_validate(record.model_dump(mode="python"))


def _assignment_response(record: ReviewerAssignment) -> AssignmentResponse:
    return AssignmentResponse.model_validate(record.model_dump(mode="python"))


def _error_code_response(record: AnnotationErrorCode) -> ErrorCodeResponse:
    return ErrorCodeResponse.model_validate(record.model_dump(mode="python"))


def _adjudication_response(record: AdjudicationRecord) -> AdjudicationResponse:
    return AdjudicationResponse.model_validate(record.model_dump(mode="python"))


def _raise_http(exc: EvaluationError) -> NoReturn:
    if isinstance(exc, EvaluationNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ConcurrencyConflict, DuplicateEvaluationRecord)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, InvalidEvaluationState):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail="evaluation operation failed") from exc
