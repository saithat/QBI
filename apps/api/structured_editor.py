"""Typed western-blot annotation editor API."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from hiveblot_auth import AuthorizationService
from hiveblot_contracts import (
    AnnotationDocumentRecord,
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    CanonicalEntityType,
    ResourceScope,
    WesternBlotStructuredAnnotation,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationNotFound,
    InvalidEvaluationState,
    StructuredAnnotationService,
)

from .auth_dependencies import (
    AuthorizationServiceDependency,
    PrincipalDependency,
    RequestIdDependency,
)
from .authorization import (
    annotation_creation_scope,
    require_actor,
    require_resource,
    require_scope,
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
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
    reviewer_id: UUID,
    prediction_id: UUID | None = None,
) -> StructuredEditorResponse:
    require_actor(principal, reviewer_id, field_name="reviewer_id")
    require_resource(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        target_type="evaluation_case",
        target_id=case_id,
        request_id=request_id,
    )
    try:
        current = service.reviewer_annotation(case_id, reviewer_id)
        if current is not None and not authorization.is_allowed(
            principal,
            AuthorizationPermission.ANNOTATION_READ,
            scope=_document_scope(current[0]),
        ):
            current = None
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
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> AnnotationMutationResponse:
    require_actor(principal, request.reviewer_id, field_name="reviewer_id")
    case_scope = require_resource(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_REVIEW,
        target_type="evaluation_case",
        target_id=case_id,
        request_id=request_id,
    )
    try:
        current = service.reviewer_annotation(case_id, request.reviewer_id)
        scope = _editor_scope(
            current[0] if current is not None else None,
            case_scope=case_scope,
            requested_visibility=request.visibility,
            requested_organization_id=request.organization_id,
            principal=principal,
            authorization=authorization,
            request_id=request_id,
        )
        document, revision = service.save(
            case_id,
            reviewer_id=request.reviewer_id,
            expected_head_revision_id=request.expected_head_revision_id,
            annotation=request.annotation,
            rationale=request.rationale,
            error_codes=request.error_codes,
            visibility=scope.visibility,
            organization_id=scope.organization_id,
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
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> StructuredAnnotationDraftResponse:
    require_resource(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        target_type="evaluation_case",
        target_id=case_id,
        request_id=request_id,
    )
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
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> AnnotationMutationResponse:
    require_actor(principal, request.reviewer_id, field_name="reviewer_id")
    require_resource(
        authorization,
        principal,
        AuthorizationPermission.ANNOTATION_WRITE,
        target_type="annotation",
        target_id=annotation_id,
        request_id=request_id,
    )
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
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
    query: Annotated[str, Query(min_length=1, max_length=500)],
    entity_type: CanonicalEntityType | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> CanonicalEntityListResponse:
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        scope=ResourceScope(visibility=ArtifactVisibility.PUBLIC),
        target_type="canonical_entity",
        request_id=request_id,
    )
    return CanonicalEntityListResponse(
        query=query,
        entity_type=entity_type,
        candidates=service.lookup_entities(
            query=query,
            entity_type=entity_type,
            limit=limit,
        ),
    )


def _document_scope(document: AnnotationDocumentRecord) -> ResourceScope:
    return ResourceScope(
        visibility=document.visibility,
        organization_id=document.organization_id,
    )


def _editor_scope(
    document: AnnotationDocumentRecord | None,
    *,
    case_scope: ResourceScope,
    requested_visibility: str | None,
    requested_organization_id: UUID | None,
    principal: AuthenticatedPrincipal,
    authorization: AuthorizationService,
    request_id: UUID,
) -> ResourceScope:
    explicit_visibility = (
        ArtifactVisibility(requested_visibility) if requested_visibility is not None else None
    )
    if document is not None:
        existing = require_resource(
            authorization,
            principal,
            AuthorizationPermission.ANNOTATION_WRITE,
            target_type="annotation",
            target_id=document.annotation_id,
            request_id=request_id,
        )
        if explicit_visibility is not None and (
            explicit_visibility is not existing.visibility
            or requested_organization_id != existing.organization_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="annotation scope cannot change across revisions",
            )
        return existing
    scope = annotation_creation_scope(
        authorization,
        principal,
        case_scope=case_scope,
        requested_visibility=explicit_visibility,
        requested_organization_id=requested_organization_id,
    )
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.ANNOTATION_WRITE,
        scope=scope,
        target_type="annotation",
        request_id=request_id,
    )
    return scope


def _raise_http(exc: EvaluationError) -> NoReturn:
    if isinstance(exc, EvaluationNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ConcurrencyConflict, DuplicateEvaluationRecord)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, InvalidEvaluationState):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail="structured annotation operation failed") from exc
