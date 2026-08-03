"""Submission, inspection, cancellation, attempt, and log APIs for generic jobs."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from hiveblot_auth import AuthorizationService
from hiveblot_contracts import (
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    JobAttemptRecord,
    JobLogRecord,
    JobRecord,
    JobStatus,
    ResourceScope,
)
from hiveblot_job_service import (
    DuplicateJob,
    InvalidJobState,
    JobNotFound,
    JobService,
    JobServiceError,
    LeaseLost,
)
from hiveblot_storage import ArtifactStorageError
from pydantic import ValidationError

from .auth_dependencies import (
    AuthorizationServiceDependency,
    PrincipalDependency,
    RequestIdDependency,
)
from .authorization import private_creation_scope, require_actor, require_resource, require_scope
from .job_dependencies import get_job_service
from .job_schemas import CancelJobRequest, JobListResponse, SubmitJobRequest

router = APIRouter(prefix="/api/v1", tags=["generic jobs"])
JobServiceDependency = Annotated[JobService, Depends(get_job_service)]


@router.post("/jobs", response_model=JobRecord, status_code=status.HTTP_201_CREATED)
def submit_job(
    request: SubmitJobRequest,
    service: JobServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> JobRecord:
    specification = request.specification
    if specification.submitted_by is not None:
        require_actor(principal, specification.submitted_by, field_name="submitted_by")
    dependency_scopes: list[ResourceScope] = []
    if specification.parent_job_id is not None:
        dependency_scopes.append(
            require_resource(
                authorization,
                principal,
                AuthorizationPermission.JOB_READ,
                target_type="job",
                target_id=specification.parent_job_id,
                request_id=request_id,
            )
        )
    for named in specification.inputs:
        dependency_scopes.append(
            require_resource(
                authorization,
                principal,
                AuthorizationPermission.ARTIFACT_READ,
                target_type="artifact",
                target_id=named.artifact.artifact_id,
                request_id=request_id,
            )
        )
    private_dependencies = {
        item.organization_id
        for item in dependency_scopes
        if item.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
    }
    if len(private_dependencies) > 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="job dependencies cannot span organizations",
        )
    base_scope = (
        ResourceScope(
            visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
            organization_id=next(iter(private_dependencies)),
        )
        if private_dependencies
        else ResourceScope(visibility=ArtifactVisibility.PUBLIC)
    )
    explicitly_scoped = "visibility" in specification.model_fields_set
    scope = private_creation_scope(
        authorization,
        principal,
        case_scope=base_scope,
        requested_visibility=specification.visibility if explicitly_scoped else None,
        requested_organization_id=(specification.organization_id if explicitly_scoped else None),
        permission=AuthorizationPermission.JOB_SUBMIT,
        resource_name="jobs",
    )
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.JOB_SUBMIT,
        scope=scope,
        target_type="job",
        target_id=specification.job_id,
        request_id=request_id,
    )
    specification = specification.model_copy(
        update={
            "visibility": scope.visibility,
            "organization_id": scope.organization_id,
            "submitted_by": specification.submitted_by if principal.system else principal.user_id,
        }
    )
    try:
        return service.submit(specification)
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(
    service: JobServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListResponse:
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.JOB_READ,
        scope=ResourceScope(visibility=ArtifactVisibility.PUBLIC),
        target_type="job_collection",
        request_id=request_id,
    )
    try:
        records = tuple(
            service.list_jobs(
                status=job_status,
                accessible_organization_ids=authorization.organizations_with_permission(
                    principal,
                    AuthorizationPermission.JOB_READ,
                ),
                limit=limit,
                offset=offset,
            )
        )
        return JobListResponse(count=len(records), results=records)
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/jobs/{job_id}", response_model=JobRecord)
def get_job(
    job_id: UUID,
    service: JobServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> JobRecord:
    _require_job(
        job_id,
        AuthorizationPermission.JOB_READ,
        principal,
        authorization,
        request_id,
    )
    try:
        return service.get_job(job_id)
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.post("/jobs/{job_id}/cancel", response_model=JobRecord)
def cancel_job(
    job_id: UUID,
    request: CancelJobRequest,
    service: JobServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> JobRecord:
    _require_job(
        job_id,
        AuthorizationPermission.JOB_CANCEL,
        principal,
        authorization,
        request_id,
    )
    try:
        return service.cancel(job_id, reason=request.reason)
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/jobs/{job_id}/attempts", response_model=tuple[JobAttemptRecord, ...])
def list_job_attempts(
    job_id: UUID,
    service: JobServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> tuple[JobAttemptRecord, ...]:
    _require_job(
        job_id,
        AuthorizationPermission.JOB_READ,
        principal,
        authorization,
        request_id,
    )
    try:
        return tuple(service.list_attempts(job_id))
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/job-attempts/{attempt_id}/logs", response_model=tuple[JobLogRecord, ...])
def list_job_logs(
    attempt_id: UUID,
    service: JobServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> tuple[JobLogRecord, ...]:
    require_resource(
        authorization,
        principal,
        AuthorizationPermission.JOB_READ,
        target_type="job_attempt",
        target_id=attempt_id,
        request_id=request_id,
    )
    try:
        return tuple(service.list_logs(attempt_id))
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


def _require_job(
    job_id: UUID,
    permission: AuthorizationPermission,
    principal: AuthenticatedPrincipal,
    authorization: AuthorizationService,
    request_id: UUID,
) -> None:
    require_resource(
        authorization,
        principal,
        permission,
        target_type="job",
        target_id=job_id,
        request_id=request_id,
    )


def _raise_http(exc: JobServiceError | ArtifactStorageError | ValidationError) -> NoReturn:
    if isinstance(exc, JobNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (DuplicateJob, InvalidJobState, LeaseLost)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    ) from exc
