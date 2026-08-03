"""Golden-dataset lifecycle, immutable snapshot, and export API."""

from __future__ import annotations

from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from hiveblot_auth import AuthorizationService
from hiveblot_contracts import (
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    GoldenCaseState,
    GoldenCaseTransition,
    GoldenDatasetDraftDetail,
    GoldenDatasetExportObject,
    GoldenDatasetExportRecord,
    GoldenDatasetRecord,
    GoldenDatasetSnapshot,
    GoldenDatasetSplit,
    GoldenDatasetType,
    ResourceScope,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationNotFound,
    GoldenDatasetService,
    InvalidEvaluationState,
)
from hiveblot_storage import ArtifactNotFound, ArtifactStorageError
from pydantic import ValidationError

from .auth_dependencies import (
    AuthorizationServiceDependency,
    PrincipalDependency,
    RequestIdDependency,
)
from .authorization import require_actor, require_resource, require_scope
from .golden_dependencies import get_golden_dataset_service
from .golden_schemas import (
    AddGoldenCaseRequest,
    CreateGoldenDatasetRequest,
    DeleteGoldenDatasetRequest,
    FreezeGoldenDatasetRequest,
    GoldenArtifactSummaryResponse,
    GoldenCaseSnapshotSummaryResponse,
    GoldenChangeResponse,
    GoldenDatasetDetailResponse,
    GoldenDatasetListResponse,
    GoldenDatasetMemberResponse,
    GoldenDatasetResponse,
    GoldenDatasetSnapshotResponse,
    GoldenExportDownloadResponse,
    GoldenExportObjectResponse,
    GoldenExportResponse,
    GoldenTransitionListResponse,
    GoldenTransitionResponse,
    PromoteGoldenCaseRequest,
    PublishGoldenExportRequest,
)

router = APIRouter(prefix="/api/v1", tags=["golden datasets"])
GoldenServiceDependency = Annotated[
    GoldenDatasetService,
    Depends(get_golden_dataset_service),
]


@router.post(
    "/golden-datasets",
    response_model=GoldenDatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_golden_dataset(
    request: CreateGoldenDatasetRequest,
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> GoldenDatasetResponse:
    require_actor(principal, request.created_by, field_name="created_by")
    _require_public_dataset(
        authorization,
        principal,
        AuthorizationPermission.DATASET_PUBLISH,
        request_id,
        target_type="golden_dataset",
    )
    if request.predecessor_snapshot_id is not None:
        _require_golden_resource(
            "golden_snapshot",
            request.predecessor_snapshot_id,
            AuthorizationPermission.DATASET_READ,
            authorization,
            principal,
            request_id,
        )
    try:
        record = service.create_dataset(
            dataset_name=request.dataset_name,
            dataset_version=request.dataset_version,
            dataset_type=GoldenDatasetType(request.dataset_type),
            predecessor_snapshot_id=request.predecessor_snapshot_id,
            created_by=request.created_by,
        )
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _dataset_response(record)


@router.get("/golden-datasets", response_model=GoldenDatasetListResponse)
def list_golden_datasets(
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> GoldenDatasetListResponse:
    _require_public_dataset(
        authorization,
        principal,
        AuthorizationPermission.DATASET_READ,
        request_id,
        target_type="golden_dataset_collection",
    )
    try:
        records = service.list_datasets(limit=limit)
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return GoldenDatasetListResponse(
        datasets=tuple(_dataset_response(record) for record in records)
    )


@router.get(
    "/golden-datasets/{dataset_id}",
    response_model=GoldenDatasetDetailResponse,
)
def get_golden_dataset(
    dataset_id: UUID,
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> GoldenDatasetDetailResponse:
    _require_golden_resource(
        "golden_dataset",
        dataset_id,
        AuthorizationPermission.DATASET_READ,
        authorization,
        principal,
        request_id,
    )
    try:
        detail = service.get_detail(dataset_id)
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _detail_response(detail)


@router.delete(
    "/golden-datasets/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_golden_dataset(
    dataset_id: UUID,
    request: DeleteGoldenDatasetRequest,
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> Response:
    _require_golden_resource(
        "golden_dataset",
        dataset_id,
        AuthorizationPermission.DATASET_PUBLISH,
        authorization,
        principal,
        request_id,
    )
    try:
        service.delete_draft(
            dataset_id,
            expected_revision=request.expected_dataset_revision,
        )
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/golden-datasets/{dataset_id}/cases/{case_id}",
    response_model=GoldenDatasetDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_golden_case(
    dataset_id: UUID,
    case_id: UUID,
    request: AddGoldenCaseRequest,
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> GoldenDatasetDetailResponse:
    require_actor(principal, request.actor_id, field_name="actor_id")
    _require_golden_resource(
        "golden_dataset",
        dataset_id,
        AuthorizationPermission.DATASET_PUBLISH,
        authorization,
        principal,
        request_id,
    )
    case_scope = require_resource(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        target_type="evaluation_case",
        target_id=case_id,
        request_id=request_id,
    )
    if case_scope.visibility is not ArtifactVisibility.PUBLIC:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="shared golden datasets cannot include organization-private cases",
        )
    try:
        detail = service.add_case(
            dataset_id,
            case_id,
            expected_dataset_revision=request.expected_dataset_revision,
            paper_key=request.paper_key,
            split=GoldenDatasetSplit(request.split),
            actor_id=request.actor_id,
            rationale=request.rationale,
        )
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _detail_response(detail)


@router.post(
    "/golden-datasets/{dataset_id}/cases/{case_id}/promotions",
    response_model=GoldenDatasetDetailResponse,
)
def promote_golden_case(
    dataset_id: UUID,
    case_id: UUID,
    request: PromoteGoldenCaseRequest,
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> GoldenDatasetDetailResponse:
    require_actor(principal, request.actor_id, field_name="actor_id")
    _require_golden_resource(
        "golden_dataset",
        dataset_id,
        AuthorizationPermission.DATASET_PUBLISH,
        authorization,
        principal,
        request_id,
    )
    require_resource(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        target_type="evaluation_case",
        target_id=case_id,
        request_id=request_id,
    )
    if request.selected_revision_id is not None:
        require_resource(
            authorization,
            principal,
            AuthorizationPermission.ANNOTATION_READ,
            target_type="annotation_revision",
            target_id=request.selected_revision_id,
            request_id=request_id,
        )
    try:
        detail = service.promote_case(
            dataset_id,
            case_id,
            expected_dataset_revision=request.expected_dataset_revision,
            expected_state_version=request.expected_state_version,
            target_state=GoldenCaseState(request.target_state),
            selected_revision_id=request.selected_revision_id,
            actor_id=request.actor_id,
            rationale=request.rationale,
        )
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _detail_response(detail)


@router.get(
    "/golden-datasets/{dataset_id}/cases/{case_id}/transitions",
    response_model=GoldenTransitionListResponse,
)
def list_golden_case_transitions(
    dataset_id: UUID,
    case_id: UUID,
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> GoldenTransitionListResponse:
    _require_golden_resource(
        "golden_dataset",
        dataset_id,
        AuthorizationPermission.DATASET_READ,
        authorization,
        principal,
        request_id,
    )
    require_resource(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        target_type="evaluation_case",
        target_id=case_id,
        request_id=request_id,
    )
    try:
        transitions = service.list_case_transitions(dataset_id, case_id)
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return GoldenTransitionListResponse(
        dataset_id=dataset_id,
        case_id=case_id,
        transitions=tuple(_transition_response(item) for item in transitions),
    )


@router.post(
    "/golden-datasets/{dataset_id}/snapshots",
    response_model=GoldenDatasetSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
def freeze_golden_dataset(
    dataset_id: UUID,
    request: FreezeGoldenDatasetRequest,
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> GoldenDatasetSnapshotResponse:
    require_actor(principal, request.frozen_by, field_name="frozen_by")
    _require_golden_resource(
        "golden_dataset",
        dataset_id,
        AuthorizationPermission.DATASET_PUBLISH,
        authorization,
        principal,
        request_id,
    )
    try:
        snapshot = service.freeze(
            dataset_id,
            expected_dataset_revision=request.expected_dataset_revision,
            frozen_by=request.frozen_by,
        )
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _snapshot_response(snapshot)


@router.get(
    "/golden-dataset-snapshots/{snapshot_id}",
    response_model=GoldenDatasetSnapshotResponse,
)
def get_golden_snapshot(
    snapshot_id: UUID,
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> GoldenDatasetSnapshotResponse:
    _require_golden_resource(
        "golden_snapshot",
        snapshot_id,
        AuthorizationPermission.DATASET_READ,
        authorization,
        principal,
        request_id,
    )
    try:
        snapshot = service.get_snapshot(snapshot_id)
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _snapshot_response(snapshot)


@router.post(
    "/golden-dataset-snapshots/{snapshot_id}/exports",
    response_model=GoldenExportResponse,
    status_code=status.HTTP_201_CREATED,
)
def publish_golden_export(
    snapshot_id: UUID,
    request: PublishGoldenExportRequest,
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> GoldenExportResponse:
    _require_golden_resource(
        "golden_snapshot",
        snapshot_id,
        AuthorizationPermission.DATASET_PUBLISH,
        authorization,
        principal,
        request_id,
    )
    del request
    try:
        record = service.publish_export(snapshot_id)
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _export_response(record)


@router.get(
    "/golden-dataset-snapshots/{snapshot_id}/exports",
    response_model=GoldenExportResponse,
)
def get_golden_export(
    snapshot_id: UUID,
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> GoldenExportResponse:
    _require_golden_resource(
        "golden_snapshot",
        snapshot_id,
        AuthorizationPermission.DATASET_READ,
        authorization,
        principal,
        request_id,
    )
    try:
        record = service.get_export(snapshot_id)
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _export_response(record)


@router.post(
    "/golden-dataset-snapshots/{snapshot_id}/exports/{export_kind}/download-url",
    response_model=GoldenExportDownloadResponse,
)
def create_golden_export_download_url(
    snapshot_id: UUID,
    export_kind: Literal["cases_jsonl", "manifest_json"],
    service: GoldenServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> GoldenExportDownloadResponse:
    _require_golden_resource(
        "golden_snapshot",
        snapshot_id,
        AuthorizationPermission.DATASET_READ,
        authorization,
        principal,
        request_id,
    )
    try:
        record = service.get_export(snapshot_id)
        url, expires_at = service.create_export_download_url(
            snapshot_id,
            export_kind=export_kind,
        )
    except (EvaluationError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return GoldenExportDownloadResponse(
        export_id=record.export_id,
        export_kind=export_kind,
        url=url,
        expires_at=expires_at,
    )


def _require_public_dataset(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    permission: AuthorizationPermission,
    request_id: UUID,
    *,
    target_type: str,
) -> None:
    require_scope(
        authorization,
        principal,
        permission,
        scope=ResourceScope(visibility=ArtifactVisibility.PUBLIC),
        target_type=target_type,
        request_id=request_id,
    )


def _require_golden_resource(
    target_type: str,
    target_id: UUID,
    permission: AuthorizationPermission,
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    request_id: UUID,
) -> None:
    require_resource(
        authorization,
        principal,
        permission,
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
    )


def _dataset_response(value: GoldenDatasetRecord) -> GoldenDatasetResponse:
    return GoldenDatasetResponse(
        dataset_id=value.dataset_id,
        dataset_name=value.dataset_name,
        dataset_version=value.dataset_version,
        dataset_type=value.dataset_type.value,
        status=value.status.value,
        predecessor_snapshot_id=value.predecessor_snapshot_id,
        revision=value.revision,
        created_by=value.created_by,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _detail_response(value: GoldenDatasetDraftDetail) -> GoldenDatasetDetailResponse:
    return GoldenDatasetDetailResponse(
        dataset=_dataset_response(value.dataset),
        members=tuple(
            GoldenDatasetMemberResponse(
                case_id=item.case_id,
                paper_key=item.paper_key,
                split=item.split.value,
                state=item.state.value,
                selected_revision_id=item.selected_revision_id,
                state_version=item.state_version,
                added_by=item.added_by,
                added_at=item.added_at,
                updated_at=item.updated_at,
            )
            for item in value.members
        ),
    )


def _transition_response(value: GoldenCaseTransition) -> GoldenTransitionResponse:
    return GoldenTransitionResponse(
        transition_id=value.transition_id,
        case_id=value.case_id,
        from_state=value.from_state.value if value.from_state is not None else None,
        to_state=value.to_state.value,
        selected_revision_id=value.selected_revision_id,
        actor_id=value.actor_id,
        rationale=value.rationale,
        created_at=value.created_at,
    )


def _snapshot_response(value: GoldenDatasetSnapshot) -> GoldenDatasetSnapshotResponse:
    return GoldenDatasetSnapshotResponse(
        snapshot_id=value.snapshot_id,
        dataset_id=value.dataset_id,
        dataset_name=value.dataset_name,
        dataset_version=value.dataset_version,
        dataset_type=value.dataset_type.value,
        predecessor_snapshot_id=value.predecessor_snapshot_id,
        content_sha256=value.content_sha256,
        frozen_by=value.frozen_by,
        frozen_at=value.frozen_at,
        cases=tuple(
            GoldenCaseSnapshotSummaryResponse(
                case_id=item.case_id,
                case_key=item.case_key,
                paper_key=item.paper_key,
                split=item.split.value,
                annotation_revision_id=item.annotation_revision.revision_id,
                annotation_schema_version=item.annotation_revision.schema_version,
                review_status=item.provenance.review_status.value,
                adjudication_id=item.provenance.adjudication_id,
                artifacts=tuple(
                    GoldenArtifactSummaryResponse(
                        artifact_id=source.artifact.artifact_id,
                        role=source.role.value,
                        page_number=source.page_number,
                        sha256=source.artifact.sha256,
                        media_type=source.artifact.media_type,
                        byte_size=source.artifact.byte_size,
                    )
                    for source in item.source_artifacts
                ),
            )
            for item in value.cases
        ),
        changes=tuple(
            GoldenChangeResponse(
                case_id=item.case_id,
                change_type=item.change_type.value,
                changed_fields=tuple(field.value for field in item.changed_fields),
                previous_case_sha256=item.previous_case_sha256,
                current_case_sha256=item.current_case_sha256,
                previous_split=(item.previous_split.value if item.previous_split else None),
                current_split=(item.current_split.value if item.current_split else None),
                previous_revision_id=item.previous_revision_id,
                current_revision_id=item.current_revision_id,
            )
            for item in value.changelog.changes
        ),
    )


def _export_object_response(value: GoldenDatasetExportObject) -> GoldenExportObjectResponse:
    return GoldenExportObjectResponse(
        filename=value.filename,
        media_type=value.media_type,
        sha256=value.sha256,
        byte_size=value.byte_size,
        storage_key=value.storage_key,
    )


def _export_response(value: GoldenDatasetExportRecord) -> GoldenExportResponse:
    return GoldenExportResponse(
        export_id=value.export_id,
        snapshot_id=value.snapshot_id,
        cases_jsonl=_export_object_response(value.cases_jsonl),
        manifest_json=_export_object_response(value.manifest_json),
        created_at=value.created_at,
    )


def _raise_http(
    exc: EvaluationError | ArtifactStorageError | ValidationError | ValueError,
) -> NoReturn:
    if isinstance(exc, EvaluationNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ArtifactNotFound):
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=str(exc),
        ) from exc
    if isinstance(exc, (ConcurrencyConflict, DuplicateEvaluationRecord)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, (InvalidEvaluationState, ArtifactStorageError, ValidationError, ValueError)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    raise HTTPException(status_code=500, detail="golden dataset operation failed") from exc
