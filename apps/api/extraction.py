"""Stored PDF/image western-blot extraction and selective replay API."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from hiveblot_auth import AuthorizationService
from hiveblot_contracts import (
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    ResourceScope,
    WesternBlotComponentReplayRecord,
    WesternBlotExtractionConfiguration,
    WesternBlotExtractionImplementation,
    WesternBlotExtractionRunRecord,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationNotFound,
)
from hiveblot_extraction import (
    ExtractionError,
    ExtractionImplementationNotFound,
    ExtractionOutputInvalid,
    UnsupportedExtractionSource,
    WesternBlotExtractionService,
)
from hiveblot_storage import ArtifactNotFound, ArtifactStorageError
from pydantic import ValidationError

from .auth_dependencies import (
    AuthorizationServiceDependency,
    PrincipalDependency,
    RequestIdDependency,
)
from .authorization import require_resource, require_scope
from .extraction_dependencies import get_western_blot_extraction_service
from .extraction_schemas import (
    ReplayWesternBlotComponentRequest,
    StartWesternBlotExtractionRequest,
    WesternBlotComponentReplayResponse,
    WesternBlotExtractionRunResponse,
    WesternBlotImplementationListResponse,
    WesternBlotImplementationResponse,
)

router = APIRouter(prefix="/api/v1", tags=["western blot extraction"])
ExtractionServiceDependency = Annotated[
    WesternBlotExtractionService,
    Depends(get_western_blot_extraction_service),
]


@router.get(
    "/western-blot-extraction-implementations",
    response_model=WesternBlotImplementationListResponse,
)
def list_western_blot_extraction_implementations(
    service: ExtractionServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> WesternBlotImplementationListResponse:
    _require_public_read(authorization, principal, request_id)
    return WesternBlotImplementationListResponse(
        implementations=tuple(
            _implementation_response(item) for item in service.list_implementations()
        )
    )


@router.post(
    "/western-blot-extractions",
    response_model=WesternBlotExtractionRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_western_blot_extraction(
    request: StartWesternBlotExtractionRequest,
    service: ExtractionServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> WesternBlotExtractionRunResponse:
    source_scope = require_resource(
        authorization,
        principal,
        AuthorizationPermission.ARTIFACT_READ,
        target_type="artifact",
        target_id=request.source_artifact_id,
        request_id=request_id,
    )
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_MANAGE,
        scope=source_scope,
        target_type="western_blot_extraction",
        target_id=request.source_artifact_id,
        request_id=request_id,
    )
    try:
        record = service.run(
            request.source_artifact_id,
            configuration=WesternBlotExtractionConfiguration(
                implementation_name=request.implementation_name,
                dpi=request.dpi,
                minimum_candidate_score=request.minimum_candidate_score,
                minimum_model_score=request.minimum_model_score,
                maximum_candidates=request.maximum_candidates,
                image_max_side=request.image_max_side,
                model_max_tokens=request.model_max_tokens,
            ),
            trace_id=request.trace_id,
        )
    except (
        EvaluationError,
        ExtractionError,
        ArtifactStorageError,
        ValidationError,
        ValueError,
        requests.RequestException,
    ) as exc:
        _raise_http(exc)
    return _run_response(record)


@router.post(
    "/western-blot-extraction-invocations/{invocation_id}/replays",
    response_model=WesternBlotComponentReplayResponse,
    status_code=status.HTTP_201_CREATED,
)
def replay_western_blot_extraction_component(
    invocation_id: UUID,
    request: ReplayWesternBlotComponentRequest,
    service: ExtractionServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> WesternBlotComponentReplayResponse:
    require_resource(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_MANAGE,
        target_type="component_invocation",
        target_id=invocation_id,
        request_id=request_id,
    )
    try:
        record = service.replay_component(invocation_id, trace_id=request.trace_id)
    except (
        EvaluationError,
        ExtractionError,
        ArtifactStorageError,
        ValidationError,
        ValueError,
        requests.RequestException,
    ) as exc:
        _raise_http(exc)
    return _replay_response(record)


def _require_public_read(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    request_id: UUID,
) -> None:
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_READ,
        scope=ResourceScope(visibility=ArtifactVisibility.PUBLIC),
        target_type="western_blot_extraction_implementation",
        request_id=request_id,
    )


def _implementation_response(
    value: WesternBlotExtractionImplementation,
) -> WesternBlotImplementationResponse:
    return WesternBlotImplementationResponse(
        implementation_name=value.implementation_name,
        implementation_version=value.implementation_version,
        pipeline_name=value.pipeline.name,
        pipeline_version=value.pipeline.version,
        detector_name=value.detector.name,
        detector_version=value.detector.version,
        model_provider=value.model.provider,
        model_name=value.model.name,
        model_version=value.model.version,
        prompt_version=value.prompt_version,
        assembler_name=value.assembler.name,
        assembler_version=value.assembler.version,
    )


def _run_response(value: WesternBlotExtractionRunRecord) -> WesternBlotExtractionRunResponse:
    predictions = value.result.candidate_predictions.predictions
    return WesternBlotExtractionRunResponse(
        case_id=value.case_id,
        run_id=value.run_id,
        definition_id=value.definition_id,
        detector_invocation_id=value.detector_invocation_id,
        model_invocation_id=value.model_invocation_id,
        assembler_invocation_id=value.assembler_invocation_id,
        prediction_id=value.prediction_id,
        publication_id=value.publication_id,
        trace_id=value.trace_id,
        pipeline_name=value.pipeline.name,
        pipeline_version=value.pipeline.version,
        source_artifact_id=value.result.source_artifact.artifact_id,
        source_sha256=value.result.source_artifact.sha256,
        candidate_count=len(value.result.candidate_predictions.figure_candidates.candidates),
        relevant_candidate_count=sum(item.is_western_blot for item in predictions),
        panel_count=sum(len(item.panels) for item in predictions),
        protein_count=len(value.result.structured_annotation.proteins),
        lane_count=len(value.result.structured_annotation.lane_conditions),
        region_count=len(value.result.spatial_annotation_set.spatial_annotations),
        warning_count=len(value.result.validation_issues),
        confidence=value.result.confidence,
    )


def _replay_response(
    value: WesternBlotComponentReplayRecord,
) -> WesternBlotComponentReplayResponse:
    return WesternBlotComponentReplayResponse(
        run_id=value.run_id,
        case_id=value.case_id,
        invocation_id=value.invocation_id,
        replay_of_invocation_id=value.replay_of_invocation_id,
        component_key=value.component_key,
        trace_id=value.trace_id,
        prediction_id=value.prediction_id,
        publication_id=value.publication_id,
    )


def _raise_http(exc: Exception) -> NoReturn:
    if isinstance(exc, (EvaluationNotFound, ArtifactNotFound)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (ConcurrencyConflict, DuplicateEvaluationRecord)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, requests.RequestException):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            ExtractionImplementationNotFound,
            ExtractionOutputInvalid,
            UnsupportedExtractionSource,
            ExtractionError,
            EvaluationError,
            ArtifactStorageError,
            ValidationError,
            ValueError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    raise HTTPException(status_code=500, detail="western-blot extraction failed") from exc
