"""Deterministic densitometry workbench, execution, and replay API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from hiveblot_auth import AuthorizationService
from hiveblot_contracts import (
    ArtifactRecord,
    ArtifactReference,
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    PredictionGeometryReference,
    ResourceScope,
    ReviewerGeometryReference,
)
from hiveblot_densitometry import (
    DensitometryError,
    DensitometryGeometryOption,
    DensitometryRunNotFound,
    DensitometryService,
    StoredDensitometryAttempt,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationNotFound,
)
from hiveblot_storage import ArtifactNotFound, ArtifactService, ArtifactStorageError
from pydantic import ValidationError

from .artifact_dependencies import get_artifact_service
from .auth_dependencies import (
    AuthorizationServiceDependency,
    PrincipalDependency,
    RequestIdDependency,
)
from .authorization import (
    private_creation_scope,
    require_resource,
    require_scope,
    visible_records,
)
from .densitometry_dependencies import get_densitometry_service
from .densitometry_schemas import (
    DensitometryAttemptResponse,
    DensitometryGeometryOptionResponse,
    DensitometryWorkbenchResponse,
    ReplayDensitometryRequest,
    StartDensitometryRunRequest,
)

router = APIRouter(prefix="/api/v1", tags=["densitometry"])
DensitometryServiceDependency = Annotated[
    DensitometryService,
    Depends(get_densitometry_service),
]
ArtifactServiceDependency = Annotated[ArtifactService, Depends(get_artifact_service)]


@router.get(
    "/evaluation-cases/{case_id}/densitometry-workbench",
    response_model=DensitometryWorkbenchResponse,
)
def get_densitometry_workbench(
    case_id: UUID,
    service: DensitometryServiceDependency,
    artifacts: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> DensitometryWorkbenchResponse:
    _require_case(
        case_id,
        AuthorizationPermission.EVALUATION_READ,
        principal,
        authorization,
        request_id,
    )
    try:
        geometry_options = visible_records(
            service.geometry_options(case_id),
            authorization=authorization,
            principal=principal,
            permission=AuthorizationPermission.ANNOTATION_READ,
            scope_of=lambda option: ResourceScope(
                visibility=option.visibility,
                organization_id=option.organization_id,
            ),
        )
        attempts = visible_records(
            service.list_attempts(case_id),
            authorization=authorization,
            principal=principal,
            permission=AuthorizationPermission.TRACE_READ,
            scope_of=lambda attempt: ResourceScope(
                visibility=attempt.visibility,
                organization_id=attempt.organization_id,
            ),
        )
        return DensitometryWorkbenchResponse(
            case_id=case_id,
            geometry_options=tuple(
                _geometry_response(
                    item,
                    artifacts,
                    principal,
                    authorization,
                    request_id,
                )
                for item in geometry_options
            ),
            attempts=tuple(
                _attempt_response(
                    item,
                    artifacts,
                    principal,
                    authorization,
                    request_id,
                )
                for item in attempts
            ),
        )
    except (DensitometryError, EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.post(
    "/evaluation-cases/{case_id}/densitometry-runs",
    response_model=DensitometryAttemptResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_densitometry_run(
    case_id: UUID,
    request: StartDensitometryRunRequest,
    service: DensitometryServiceDependency,
    artifacts: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> DensitometryAttemptResponse:
    case_scope = _require_case(
        case_id,
        AuthorizationPermission.EVALUATION_REVIEW,
        principal,
        authorization,
        request_id,
    )
    run_scope = private_creation_scope(
        authorization,
        principal,
        case_scope=case_scope,
        requested_visibility=(
            ArtifactVisibility(request.visibility) if request.visibility is not None else None
        ),
        requested_organization_id=request.organization_id,
        permission=AuthorizationPermission.EVALUATION_REVIEW,
        resource_name="densitometry runs",
    )
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_REVIEW,
        scope=run_scope,
        target_type="pipeline_run",
        request_id=request_id,
    )
    require_resource(
        authorization,
        principal,
        AuthorizationPermission.ARTIFACT_READ,
        target_type="artifact",
        target_id=request.image_artifact_id,
        request_id=request_id,
    )
    _authorize_geometry(request.geometry, principal, authorization, request_id)
    try:
        run = service.run(
            case_id,
            image_artifact_id=request.image_artifact_id,
            geometry=request.geometry,
            loading_control_target_id=request.loading_control_target_id,
            configuration=request.configuration.to_contract(),
            trace_id=request.trace_id,
            actor_id=principal.user_id,
            visibility=run_scope.visibility,
            organization_id=run_scope.organization_id,
        )
        return _attempt_response(
            service.get_attempt(run.invocation_id),
            artifacts,
            principal,
            authorization,
            request_id,
        )
    except (DensitometryError, EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get(
    "/densitometry-invocations/{invocation_id}",
    response_model=DensitometryAttemptResponse,
)
def get_densitometry_attempt(
    invocation_id: UUID,
    service: DensitometryServiceDependency,
    artifacts: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> DensitometryAttemptResponse:
    _require_invocation(
        invocation_id,
        AuthorizationPermission.TRACE_READ,
        principal,
        authorization,
        request_id,
    )
    try:
        return _attempt_response(
            service.get_attempt(invocation_id),
            artifacts,
            principal,
            authorization,
            request_id,
        )
    except (DensitometryError, EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.post(
    "/densitometry-invocations/{invocation_id}/replays",
    response_model=DensitometryAttemptResponse,
    status_code=status.HTTP_201_CREATED,
)
def replay_densitometry_attempt(
    invocation_id: UUID,
    request: ReplayDensitometryRequest,
    service: DensitometryServiceDependency,
    artifacts: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> DensitometryAttemptResponse:
    _require_invocation(
        invocation_id,
        AuthorizationPermission.EVALUATION_REVIEW,
        principal,
        authorization,
        request_id,
    )
    try:
        replay = service.replay(
            invocation_id,
            trace_id=request.trace_id,
            actor_id=principal.user_id,
        )
        return _attempt_response(
            service.get_attempt(replay.invocation_id),
            artifacts,
            principal,
            authorization,
            request_id,
        )
    except (DensitometryError, EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


def _geometry_response(
    value: DensitometryGeometryOption,
    artifacts: ArtifactService,
    principal: AuthenticatedPrincipal,
    authorization: AuthorizationService,
    request_id: UUID,
) -> DensitometryGeometryOptionResponse:
    url, expires_at = _download_url(
        value.image_artifact.artifact_id,
        artifacts,
        principal,
        authorization,
        request_id,
    )
    return DensitometryGeometryOptionResponse(
        geometry=value.geometry,
        label=value.label,
        created_at=value.created_at,
        image_artifact=_reference(value.image_artifact),
        image_kind=value.image_kind,
        image_download_url=url,
        image_download_expires_at=expires_at,
        lanes=value.lanes,
        targets=value.targets,
        bands=value.bands,
        inferred_loading_control_target_ids=value.inferred_loading_control_target_ids,
    )


def _attempt_response(
    value: StoredDensitometryAttempt,
    artifacts: ArtifactService,
    principal: AuthenticatedPrincipal,
    authorization: AuthorizationService,
    request_id: UUID,
) -> DensitometryAttemptResponse:
    source_url, source_expires = _download_url(
        value.result.input.image_artifact.artifact_id,
        artifacts,
        principal,
        authorization,
        request_id,
    )
    overlay_url, overlay_expires = _download_url(
        value.result.analysis_overlay.artifact_id,
        artifacts,
        principal,
        authorization,
        request_id,
    )
    return DensitometryAttemptResponse(
        run_id=value.run_id,
        definition_id=value.definition_id,
        case_id=value.case_id,
        invocation_id=value.invocation_id,
        replay_of_invocation_id=value.replay_of_invocation_id,
        publication_id=value.publication_id,
        trace_id=value.trace_id,
        visibility=value.visibility.value,
        organization_id=value.organization_id,
        created_at=value.created_at,
        result=value.result,
        source_image_download_url=source_url,
        source_image_download_expires_at=source_expires,
        overlay_download_url=overlay_url,
        overlay_download_expires_at=overlay_expires,
    )


def _download_url(
    artifact_id: UUID,
    artifacts: ArtifactService,
    principal: AuthenticatedPrincipal,
    authorization: AuthorizationService,
    request_id: UUID,
) -> tuple[str, datetime]:
    require_resource(
        authorization,
        principal,
        AuthorizationPermission.ARTIFACT_READ,
        target_type="artifact",
        target_id=artifact_id,
        request_id=request_id,
    )
    return artifacts.create_download_url(artifact_id, actor_id=principal.user_id)


def _require_case(
    case_id: UUID,
    permission: AuthorizationPermission,
    principal: AuthenticatedPrincipal,
    authorization: AuthorizationService,
    request_id: UUID,
) -> ResourceScope:
    return require_resource(
        authorization,
        principal,
        permission,
        target_type="evaluation_case",
        target_id=case_id,
        request_id=request_id,
    )


def _authorize_geometry(
    geometry: PredictionGeometryReference | ReviewerGeometryReference,
    principal: AuthenticatedPrincipal,
    authorization: AuthorizationService,
    request_id: UUID,
) -> None:
    if isinstance(geometry, ReviewerGeometryReference):
        target_type = "annotation_revision"
        target_id = geometry.annotation_revision_id
        permission = AuthorizationPermission.ANNOTATION_READ
    else:
        target_type = "prediction"
        target_id = geometry.prediction_id
        permission = AuthorizationPermission.EVALUATION_READ
    require_resource(
        authorization,
        principal,
        permission,
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
    )


def _require_invocation(
    invocation_id: UUID,
    permission: AuthorizationPermission,
    principal: AuthenticatedPrincipal,
    authorization: AuthorizationService,
    request_id: UUID,
) -> None:
    require_resource(
        authorization,
        principal,
        permission,
        target_type="component_invocation",
        target_id=invocation_id,
        request_id=request_id,
    )


def _reference(value: ArtifactRecord) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=value.artifact_id,
        sha256=value.sha256,
        media_type=value.media_type,
        byte_size=value.byte_size,
    )


def _raise_http(
    exc: DensitometryError | EvaluationError | ArtifactStorageError | ValidationError,
) -> NoReturn:
    if isinstance(exc, (DensitometryRunNotFound, EvaluationNotFound, ArtifactNotFound)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (ConcurrencyConflict, DuplicateEvaluationRecord)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    ) from exc
