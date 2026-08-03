"""Paginated review queue and saved-filter API."""

from __future__ import annotations

from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from hiveblot_auth import AuthorizationService
from hiveblot_contracts import (
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    RegressionStatus,
    ResourceScope,
    ReviewQueueCaseSummary,
    ReviewQueueFilters,
    ReviewQueuePage,
    ReviewStatus,
    SavedReviewView,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationNotFound,
    ReviewQueueService,
)
from pydantic import ValidationError

from .auth_dependencies import (
    AuthorizationServiceDependency,
    PrincipalDependency,
    RequestIdDependency,
)
from .authorization import require_actor, require_scope
from .review_queue_dependencies import get_review_queue_service
from .review_queue_schemas import (
    CreateSavedReviewViewRequest,
    ReviewQueueCaseResponse,
    ReviewQueueFiltersInput,
    ReviewQueuePageResponse,
    SavedReviewViewListResponse,
    SavedReviewViewResponse,
    UpdateSavedReviewViewRequest,
)

router = APIRouter(prefix="/api/v1", tags=["review queue"])
ReviewQueueServiceDependency = Annotated[ReviewQueueService, Depends(get_review_queue_service)]


@router.get("/review-queue", response_model=ReviewQueuePageResponse)
def browse_review_queue(
    service: ReviewQueueServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
    review_status: Annotated[list[ReviewStatus] | None, Query()] = None,
    dataset_id: UUID | None = None,
    assay_type: Literal["western_blot"] | None = None,
    source: str | None = Query(default=None, min_length=1, max_length=500),
    prediction_version: str | None = Query(default=None, min_length=1, max_length=200),
    confidence_min: float | None = Query(default=None, ge=0, le=1),
    confidence_max: float | None = Query(default=None, ge=0, le=1),
    error_category: str | None = Query(default=None, min_length=1, max_length=200),
    reviewer_id: UUID | None = None,
    missing_provenance: bool | None = None,
    validation_warnings: bool | None = None,
    gold_eligible: bool | None = None,
    model_disagreement: bool | None = None,
    regression_status: RegressionStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ReviewQueuePageResponse:
    _authorize_review(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        request_id=request_id,
        target_type="review_queue",
    )
    try:
        filters = ReviewQueueFilters(
            review_statuses=tuple(dict.fromkeys(review_status or ())),
            dataset_id=dataset_id,
            assay_type=assay_type,
            source_query=source,
            prediction_version=prediction_version,
            confidence_min=confidence_min,
            confidence_max=confidence_max,
            error_category=error_category,
            reviewer_id=reviewer_id,
            missing_provenance=missing_provenance,
            has_validation_warnings=validation_warnings,
            gold_eligible=gold_eligible,
            model_disagreement=model_disagreement,
            regression_status=regression_status,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return _page_response(
        service.browse(
            filters,
            accessible_organization_ids=authorization.organizations_with_permission(
                principal,
                AuthorizationPermission.EVALUATION_READ,
            ),
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "/review-views",
    response_model=SavedReviewViewResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_saved_view(
    request: CreateSavedReviewViewRequest,
    service: ReviewQueueServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> SavedReviewViewResponse:
    require_actor(principal, request.owner_id, field_name="owner_id")
    _authorize_review(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        request_id=request_id,
        target_type="saved_review_view",
    )
    try:
        view = service.create_view(
            owner_id=request.owner_id,
            name=request.name,
            filters=_filters_contract(request.filters),
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return _view_response(view)


@router.get("/review-views", response_model=SavedReviewViewListResponse)
def list_saved_views(
    owner_id: UUID,
    service: ReviewQueueServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> SavedReviewViewListResponse:
    require_actor(principal, owner_id, field_name="owner_id")
    _authorize_review(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        request_id=request_id,
        target_type="saved_review_view",
    )
    return SavedReviewViewListResponse(
        owner_id=owner_id,
        views=tuple(_view_response(view) for view in service.list_views(owner_id)),
    )


@router.get("/review-views/{view_id}", response_model=SavedReviewViewResponse)
def get_saved_view(
    view_id: UUID,
    owner_id: UUID,
    service: ReviewQueueServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> SavedReviewViewResponse:
    require_actor(principal, owner_id, field_name="owner_id")
    _authorize_review(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        request_id=request_id,
        target_type="saved_review_view",
        target_id=view_id,
    )
    try:
        view = service.get_view(view_id)
        _require_owner(view, owner_id)
    except EvaluationError as exc:
        _raise_http(exc)
    return _view_response(view)


@router.put("/review-views/{view_id}", response_model=SavedReviewViewResponse)
def update_saved_view(
    view_id: UUID,
    request: UpdateSavedReviewViewRequest,
    service: ReviewQueueServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> SavedReviewViewResponse:
    require_actor(principal, request.owner_id, field_name="owner_id")
    _authorize_review(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        request_id=request_id,
        target_type="saved_review_view",
        target_id=view_id,
    )
    try:
        view = service.update_view(
            view_id,
            owner_id=request.owner_id,
            expected_version=request.expected_version,
            name=request.name,
            filters=_filters_contract(request.filters),
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return _view_response(view)


@router.delete("/review-views/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_view(
    view_id: UUID,
    service: ReviewQueueServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
    owner_id: UUID,
    expected_version: int = Query(ge=1),
) -> Response:
    require_actor(principal, owner_id, field_name="owner_id")
    _authorize_review(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        request_id=request_id,
        target_type="saved_review_view",
        target_id=view_id,
    )
    try:
        service.delete_view(
            view_id,
            owner_id=owner_id,
            expected_version=expected_version,
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _filters_contract(value: ReviewQueueFiltersInput) -> ReviewQueueFilters:
    return ReviewQueueFilters(
        review_statuses=tuple(ReviewStatus(item) for item in value.review_statuses),
        dataset_id=value.dataset_id,
        assay_type=value.assay_type,
        source_query=value.source_query,
        prediction_version=value.prediction_version,
        confidence_min=value.confidence_min,
        confidence_max=value.confidence_max,
        error_category=value.error_category,
        reviewer_id=value.reviewer_id,
        missing_provenance=value.missing_provenance,
        has_validation_warnings=value.has_validation_warnings,
        gold_eligible=value.gold_eligible,
        model_disagreement=value.model_disagreement,
        regression_status=(
            RegressionStatus(value.regression_status)
            if value.regression_status is not None
            else None
        ),
    )


def _authorize_review(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    permission: AuthorizationPermission,
    *,
    request_id: UUID,
    target_type: str,
    target_id: UUID | None = None,
) -> None:
    require_scope(
        authorization,
        principal,
        permission,
        scope=ResourceScope(visibility=ArtifactVisibility.PUBLIC),
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
    )


def _filters_response(value: ReviewQueueFilters) -> ReviewQueueFiltersInput:
    return ReviewQueueFiltersInput.model_validate_json(value.model_dump_json())


def _case_response(value: ReviewQueueCaseSummary) -> ReviewQueueCaseResponse:
    return ReviewQueueCaseResponse.model_validate_json(value.model_dump_json())


def _page_response(value: ReviewQueuePage) -> ReviewQueuePageResponse:
    return ReviewQueuePageResponse(
        filters=_filters_response(value.filters),
        items=tuple(_case_response(item) for item in value.items),
        total=value.total,
        limit=value.limit,
        offset=value.offset,
        next_offset=value.next_offset,
    )


def _view_response(value: SavedReviewView) -> SavedReviewViewResponse:
    return SavedReviewViewResponse(
        view_id=value.view_id,
        owner_id=value.owner_id,
        name=value.name,
        filters=_filters_response(value.filters),
        version=value.version,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _require_owner(view: SavedReviewView, owner_id: UUID) -> None:
    if view.owner_id != owner_id:
        raise EvaluationNotFound(f"saved review view {view.view_id} does not exist")


def _raise_http(exc: EvaluationError) -> NoReturn:
    if isinstance(exc, EvaluationNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (ConcurrencyConflict, DuplicateEvaluationRecord)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
