"""Source-pixel spatial annotation editor API."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from hiveblot_contracts import AnnotationDocumentRecord, AnnotationRevision
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationNotFound,
    InvalidEvaluationState,
    SpatialAnnotationService,
)

from .evaluation_schemas import (
    AnnotationDocumentResponse,
    AnnotationMutationResponse,
    AnnotationRevisionResponse,
    ErrorCodeResponse,
)
from .spatial_editor_dependencies import get_spatial_annotation_service
from .spatial_editor_schemas import (
    AcceptSpatialPredictionRequest,
    SaveSpatialAnnotationRequest,
    SpatialAnnotationDraftResponse,
    SpatialEditorResponse,
    SpatialPredictionOption,
    SpatialRevisionSummary,
    UndoSpatialAnnotationRequest,
)

router = APIRouter(prefix="/api/v1", tags=["spatial-annotation"])
SpatialServiceDependency = Annotated[
    SpatialAnnotationService,
    Depends(get_spatial_annotation_service),
]


@router.get(
    "/evaluation-cases/{case_id}/spatial-editor",
    response_model=SpatialEditorResponse,
)
def get_spatial_editor(
    case_id: UUID,
    service: SpatialServiceDependency,
    reviewer_id: UUID,
    prediction_id: UUID | None = None,
) -> SpatialEditorResponse:
    try:
        current = service.reviewer_annotation(case_id, reviewer_id)
        document, head = current if current is not None else (None, None)
        revisions = service.list_revisions(document.annotation_id) if document is not None else ()
        predictions = service.list_predictions(case_id)
        supported_ids: set[UUID] = set()
        for prediction in predictions:
            try:
                service.prediction_set(case_id, prediction.prediction_id)
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
        prediction_set = None
        comparison = None
        draft = service.annotation_set(head)
        if selected_id is not None:
            _, prediction_set = service.prediction_set(case_id, selected_id)
            comparison = service.compare(
                prediction_id=selected_id,
                prediction=prediction_set,
                annotation_revision_id=head.revision_id if head is not None else None,
                annotation=draft,
            )
        return SpatialEditorResponse(
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
                SpatialRevisionSummary(
                    revision_id=revision.revision_id,
                    revision_number=revision.revision_number,
                    prior_revision_id=revision.prior_revision_id,
                    rationale=revision.rationale,
                    error_codes=revision.error_codes,
                    created_at=revision.created_at,
                    spatial_annotation_count=len(revision.spatial_annotations),
                )
                for revision in revisions
            ),
            predictions=tuple(
                SpatialPredictionOption(
                    prediction_id=prediction.prediction_id,
                    prediction_schema_version=prediction.prediction_schema_version,
                    producer_name=prediction.producer.name,
                    producer_version=prediction.producer.version,
                    confidence=prediction.confidence,
                    created_at=prediction.created_at,
                    spatial_output_available=prediction.prediction_id in supported_ids,
                )
                for prediction in predictions
            ),
            selected_prediction_id=selected_id,
            prediction_set=prediction_set,
            draft_set=draft,
            comparison=comparison,
            error_codes=tuple(
                ErrorCodeResponse.model_validate(record.model_dump(mode="python"))
                for record in service.list_error_codes()
            ),
        )
    except EvaluationError as exc:
        _raise_http(exc)


@router.put(
    "/evaluation-cases/{case_id}/spatial-annotations",
    response_model=AnnotationMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def save_spatial_annotations(
    case_id: UUID,
    request: SaveSpatialAnnotationRequest,
    service: SpatialServiceDependency,
) -> AnnotationMutationResponse:
    try:
        document, revision = service.save(
            case_id,
            reviewer_id=request.reviewer_id,
            expected_head_revision_id=request.expected_head_revision_id,
            annotation_set=request.annotation_set,
            rationale=request.rationale,
            error_codes=request.error_codes,
        )
        return _mutation_response(document, revision)
    except EvaluationError as exc:
        _raise_http(exc)


@router.post(
    "/evaluation-cases/{case_id}/spatial-annotation-drafts/accept-prediction",
    response_model=SpatialAnnotationDraftResponse,
)
def accept_spatial_prediction(
    case_id: UUID,
    request: AcceptSpatialPredictionRequest,
    service: SpatialServiceDependency,
) -> SpatialAnnotationDraftResponse:
    try:
        _, prediction = service.prediction_set(case_id, request.prediction_id)
        annotation_set = service.accept_prediction(
            case_id,
            prediction_id=request.prediction_id,
            base=request.base_set,
            accepted_region_ids=request.accepted_region_ids,
            accept_all=request.accept_all,
        )
        return SpatialAnnotationDraftResponse(
            annotation_set=annotation_set,
            comparison=service.compare(
                prediction_id=request.prediction_id,
                prediction=prediction,
                annotation_revision_id=None,
                annotation=annotation_set,
            ),
        )
    except EvaluationError as exc:
        _raise_http(exc)


@router.post(
    "/annotations/{annotation_id}/spatial-undo",
    response_model=AnnotationMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def undo_spatial_annotation(
    annotation_id: UUID,
    request: UndoSpatialAnnotationRequest,
    service: SpatialServiceDependency,
) -> AnnotationMutationResponse:
    try:
        document, revision = service.undo(
            annotation_id,
            reviewer_id=request.reviewer_id,
            expected_head_revision_id=request.expected_head_revision_id,
            target_revision_id=request.target_revision_id,
            rationale=request.rationale,
        )
        return _mutation_response(document, revision)
    except EvaluationError as exc:
        _raise_http(exc)


def _mutation_response(
    document: AnnotationDocumentRecord,
    revision: AnnotationRevision,
) -> AnnotationMutationResponse:
    return AnnotationMutationResponse(
        annotation=AnnotationDocumentResponse.model_validate(document.model_dump(mode="python")),
        revision=AnnotationRevisionResponse.model_validate(revision.model_dump(mode="python")),
    )


def _raise_http(exc: EvaluationError) -> NoReturn:
    if isinstance(exc, EvaluationNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ConcurrencyConflict, DuplicateEvaluationRecord)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, InvalidEvaluationState):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail="spatial annotation operation failed") from exc
