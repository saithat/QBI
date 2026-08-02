"""Read-only source evidence workbench API and source-context registration."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from hiveblot_contracts import CaseArtifactRole, CaseSourceContext, SourceEvidenceWorkbench
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationError,
    EvaluationNotFound,
    EvidenceWorkbenchService,
)
from hiveblot_storage import ArtifactNotFound, ArtifactStorageError

from .workbench_dependencies import get_workbench_service
from .workbench_schemas import (
    PutSourceContextRequest,
    SourceContextResponse,
    SourceContextRevisionListResponse,
    SourceEvidenceWorkbenchResponse,
)

router = APIRouter(prefix="/api/v1", tags=["source evidence"])
WorkbenchServiceDependency = Annotated[
    EvidenceWorkbenchService,
    Depends(get_workbench_service),
]


@router.get(
    "/evaluation-cases/{case_id}/workbench",
    response_model=SourceEvidenceWorkbenchResponse,
)
def get_workbench(
    case_id: UUID,
    service: WorkbenchServiceDependency,
) -> SourceEvidenceWorkbenchResponse:
    try:
        workbench = service.get_workbench(case_id)
    except (EvaluationError, ArtifactStorageError) as exc:
        _raise_http(exc)
    return _workbench_response(workbench)


@router.put(
    "/evaluation-cases/{case_id}/sources/{artifact_id}/{artifact_role}/context",
    response_model=SourceContextResponse,
)
def put_source_context(
    case_id: UUID,
    artifact_id: UUID,
    artifact_role: CaseArtifactRole,
    request: PutSourceContextRequest,
    service: WorkbenchServiceDependency,
) -> SourceContextResponse:
    try:
        context = service.put_source_context(
            case_id,
            artifact_id,
            artifact_role,
            expected_head_revision_id=request.expected_head_revision_id,
            caption=request.caption,
            nearby_text=request.nearby_text,
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return _context_response(context)


@router.get(
    "/evaluation-cases/{case_id}/sources/{artifact_id}/{artifact_role}/context-revisions",
    response_model=SourceContextRevisionListResponse,
)
def list_source_context_revisions(
    case_id: UUID,
    artifact_id: UUID,
    artifact_role: CaseArtifactRole,
    service: WorkbenchServiceDependency,
) -> SourceContextRevisionListResponse:
    try:
        revisions = service.list_source_context_revisions(
            case_id,
            artifact_id,
            artifact_role,
        )
    except EvaluationError as exc:
        _raise_http(exc)
    return SourceContextRevisionListResponse(
        case_id=case_id,
        artifact_id=artifact_id,
        artifact_role=artifact_role.value,
        revisions=tuple(_context_response(revision) for revision in revisions),
    )


def _workbench_response(value: SourceEvidenceWorkbench) -> SourceEvidenceWorkbenchResponse:
    return SourceEvidenceWorkbenchResponse.model_validate_json(value.model_dump_json())


def _context_response(value: CaseSourceContext) -> SourceContextResponse:
    return SourceContextResponse.model_validate_json(value.model_dump_json())


def _raise_http(exc: EvaluationError | ArtifactStorageError) -> NoReturn:
    if isinstance(exc, ArtifactNotFound):
        raise HTTPException(status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=str(exc)) from exc
    if isinstance(exc, ArtifactStorageError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if isinstance(exc, ConcurrencyConflict):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, EvaluationNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    ) from exc
