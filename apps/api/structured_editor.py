"""Typed western-blot annotation editor API."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from hiveblot_contracts import CanonicalEntityType, WesternBlotStructuredAnnotation
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationNotFound,
    InvalidEvaluationState,
    StructuredAnnotationService,
)

from .evaluation_schemas import (
    AnnotationDocumentResponse,
    AnnotationMutationResponse,
    AnnotationRevisionResponse,
    ErrorCodeResponse,
)
from .structured_editor_dependencies import get_structured_annotation_service
from .structured_editor_schemas import (
    AcceptPredictionRequest,
    CanonicalEntityListResponse,
    PredictionEditorOption,
    RevisionEditorSummary,
    SaveStructuredAnnotationRequest,
    StructuredAnnotationDraftResponse,
    StructuredEditorResponse,
    UndoStructuredAnnotationRequest,
)

router = APIRouter(prefix="/api/v1", tags=["structured-annotation"])
StructuredServiceDependency = Annotated[
    StructuredAnnotationService,
    Depends(get_structured_annotation_service),
]


@router.get(
    "/evaluation-cases/{case_id}/structured-editor",
    response_model=StructuredEditorResponse,
)
def get_structured_editor(
    case_id: UUID,
    service: StructuredServiceDependency,
    reviewer_id: UUID,
    prediction_id: UUID | None = None,
) -> StructuredEditorResponse:
    try:
        current = service.reviewer_annotation(case_id, reviewer_id)
        document, head = current if current is not None else (None, None)
        revisions = service.list_revisions(document.annotation_id) if document is not None else ()
        predictions = service.list_predictions(case_id)
        supported_ids: set[UUID] = set()
        for prediction in predictions:
            try:
                service.prediction_annotation(case_id, prediction.prediction_id)
            except InvalidEvaluationState:
                continue
            supported_ids.add(prediction.prediction_id)
        selected_id = prediction_id
        if selected_id is None:
            selected_id = next(
                (
                    prediction.prediction_id
                    for prediction in reversed(tuple(predictions))
                    if prediction.prediction_id in supported_ids
                ),
                None,
            )
        selected_annotation = None
        comparison = None
        draft = (
            head.structured_annotation
            if head is not None and head.structured_annotation is not None
            else WesternBlotStructuredAnnotation()
        )
        if selected_id is not None:
            _, selected_annotation = service.prediction_annotation(case_id, selected_id)
            comparison = service.compare(
                prediction_id=selected_id,
                prediction=selected_annotation,
                annotation_revision_id=head.revision_id if head is not None else None,
                annotation=draft,
            )
        return StructuredEditorResponse(
            case_id=case_id,
            reviewer_id=reviewer_id,
            annotation_document=(
                AnnotationDocumentResponse.model_validate(document.model_dump(mode="python"))
                if document is not None
                else None
            ),
            head_revision=(
                AnnotationRevisionResponse.model_validate(head.model_dump(mode="python"))
                if head is not None
                else None
            ),
            revisions=tuple(
                RevisionEditorSummary(
                    revision_id=revision.revision_id,
                    revision_number=revision.revision_number,
                    prior_revision_id=revision.prior_revision_id,
                    rationale=revision.rationale,
                    error_codes=revision.error_codes,
                    created_at=revision.created_at,
                    has_structured_annotation=revision.structured_annotation is not None,
                )
                for revision in revisions
            ),
            predictions=tuple(
                PredictionEditorOption(
                    prediction_id=prediction.prediction_id,
                    prediction_schema_version=prediction.prediction_schema_version,
                    producer_name=prediction.producer.name,
                    producer_version=prediction.producer.version,
                    confidence=prediction.confidence,
                    created_at=prediction.created_at,
                    structured_output_available=prediction.prediction_id in supported_ids,
                )
                for prediction in predictions
            ),
            selected_prediction_id=selected_id,
            prediction_annotation=selected_annotation,
            draft_annotation=draft,
            comparison=comparison,
            error_codes=tuple(
                ErrorCodeResponse.model_validate(record.model_dump(mode="python"))
                for record in service.list_error_codes()
            ),
        )
    except EvaluationError as exc:
        _raise_http(exc)


@router.put(
    "/evaluation-cases/{case_id}/structured-annotations",
    response_model=AnnotationMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def save_structured_annotation(
    case_id: UUID,
    request: SaveStructuredAnnotationRequest,
    service: StructuredServiceDependency,
) -> AnnotationMutationResponse:
    try:
        document, revision = service.save(
            case_id,
            reviewer_id=request.reviewer_id,
            expected_head_revision_id=request.expected_head_revision_id,
            annotation=request.annotation,
            rationale=request.rationale,
            error_codes=request.error_codes,
        )
        return AnnotationMutationResponse(
            annotation=AnnotationDocumentResponse.model_validate(
                document.model_dump(mode="python")
            ),
            revision=AnnotationRevisionResponse.model_validate(revision.model_dump(mode="python")),
        )
    except EvaluationError as exc:
        _raise_http(exc)


@router.post(
    "/evaluation-cases/{case_id}/structured-annotation-drafts/accept-prediction",
    response_model=StructuredAnnotationDraftResponse,
)
def accept_prediction(
    case_id: UUID,
    request: AcceptPredictionRequest,
    service: StructuredServiceDependency,
) -> StructuredAnnotationDraftResponse:
    try:
        _, prediction = service.prediction_annotation(case_id, request.prediction_id)
        annotation = service.accept_prediction(
            case_id,
            prediction_id=request.prediction_id,
            base_annotation=request.base_annotation,
            accepted_entity_ids=request.accepted_entity_ids,
            accept_all=request.accept_all,
        )
        return StructuredAnnotationDraftResponse(
            annotation=annotation,
            comparison=service.compare(
                prediction_id=request.prediction_id,
                prediction=prediction,
                annotation_revision_id=None,
                annotation=annotation,
            ),
        )
    except EvaluationError as exc:
        _raise_http(exc)


@router.post(
    "/annotations/{annotation_id}/structured-undo",
    response_model=AnnotationMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def undo_structured_annotation(
    annotation_id: UUID,
    request: UndoStructuredAnnotationRequest,
    service: StructuredServiceDependency,
) -> AnnotationMutationResponse:
    try:
        document, revision = service.undo(
            annotation_id,
            reviewer_id=request.reviewer_id,
            expected_head_revision_id=request.expected_head_revision_id,
            target_revision_id=request.target_revision_id,
            rationale=request.rationale,
        )
        return AnnotationMutationResponse(
            annotation=AnnotationDocumentResponse.model_validate(
                document.model_dump(mode="python")
            ),
            revision=AnnotationRevisionResponse.model_validate(revision.model_dump(mode="python")),
        )
    except EvaluationError as exc:
        _raise_http(exc)


@router.get("/canonical-entities", response_model=CanonicalEntityListResponse)
def lookup_canonical_entities(
    service: StructuredServiceDependency,
    query: Annotated[str, Query(min_length=1, max_length=500)],
    entity_type: CanonicalEntityType | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> CanonicalEntityListResponse:
    return CanonicalEntityListResponse(
        query=query,
        entity_type=entity_type,
        candidates=service.lookup_entities(
            query=query,
            entity_type=entity_type,
            limit=limit,
        ),
    )


def _raise_http(exc: EvaluationError) -> NoReturn:
    if isinstance(exc, EvaluationNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ConcurrencyConflict, DuplicateEvaluationRecord)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, InvalidEvaluationState):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail="structured annotation operation failed") from exc
