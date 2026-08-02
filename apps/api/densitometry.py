"""Deterministic densitometry workbench, execution, and replay API."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from hiveblot_contracts import ArtifactRecord, ArtifactReference
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
) -> DensitometryWorkbenchResponse:
    try:
        return DensitometryWorkbenchResponse(
            case_id=case_id,
            geometry_options=tuple(
                _geometry_response(item, artifacts) for item in service.geometry_options(case_id)
            ),
            attempts=tuple(
                _attempt_response(item, artifacts) for item in service.list_attempts(case_id)
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
) -> DensitometryAttemptResponse:
    try:
        run = service.run(
            case_id,
            image_artifact_id=request.image_artifact_id,
            geometry=request.geometry,
            loading_control_target_id=request.loading_control_target_id,
            configuration=request.configuration.to_contract(),
            trace_id=request.trace_id,
        )
        return _attempt_response(service.get_attempt(run.invocation_id), artifacts)
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
) -> DensitometryAttemptResponse:
    try:
        return _attempt_response(service.get_attempt(invocation_id), artifacts)
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
) -> DensitometryAttemptResponse:
    try:
        replay = service.replay(invocation_id, trace_id=request.trace_id)
        return _attempt_response(service.get_attempt(replay.invocation_id), artifacts)
    except (DensitometryError, EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


def _geometry_response(
    value: DensitometryGeometryOption,
    artifacts: ArtifactService,
) -> DensitometryGeometryOptionResponse:
    url, expires_at = artifacts.create_download_url(value.image_artifact.artifact_id, actor_id=None)
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
) -> DensitometryAttemptResponse:
    source_url, source_expires = artifacts.create_download_url(
        value.result.input.image_artifact.artifact_id,
        actor_id=None,
    )
    overlay_url, overlay_expires = artifacts.create_download_url(
        value.result.analysis_overlay.artifact_id,
        actor_id=None,
    )
    return DensitometryAttemptResponse(
        run_id=value.run_id,
        definition_id=value.definition_id,
        case_id=value.case_id,
        invocation_id=value.invocation_id,
        replay_of_invocation_id=value.replay_of_invocation_id,
        publication_id=value.publication_id,
        trace_id=value.trace_id,
        created_at=value.created_at,
        result=value.result,
        source_image_download_url=source_url,
        source_image_download_expires_at=source_expires,
        overlay_download_url=overlay_url,
        overlay_download_expires_at=overlay_expires,
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
