"""Submission, inspection, cancellation, attempt, and log APIs for generic jobs."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from hiveblot_contracts import JobAttemptRecord, JobLogRecord, JobRecord, JobStatus
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

from .job_dependencies import get_job_service
from .job_schemas import CancelJobRequest, JobListResponse, SubmitJobRequest

router = APIRouter(prefix="/api/v1", tags=["generic jobs"])
JobServiceDependency = Annotated[JobService, Depends(get_job_service)]


@router.post("/jobs", response_model=JobRecord, status_code=status.HTTP_201_CREATED)
def submit_job(request: SubmitJobRequest, service: JobServiceDependency) -> JobRecord:
    try:
        return service.submit(request.specification)
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(
    service: JobServiceDependency,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListResponse:
    try:
        records = tuple(service.list_jobs(status=job_status, limit=limit, offset=offset))
        return JobListResponse(count=len(records), results=records)
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/jobs/{job_id}", response_model=JobRecord)
def get_job(job_id: UUID, service: JobServiceDependency) -> JobRecord:
    try:
        return service.get_job(job_id)
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.post("/jobs/{job_id}/cancel", response_model=JobRecord)
def cancel_job(
    job_id: UUID,
    request: CancelJobRequest,
    service: JobServiceDependency,
) -> JobRecord:
    try:
        return service.cancel(job_id, reason=request.reason)
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/jobs/{job_id}/attempts", response_model=tuple[JobAttemptRecord, ...])
def list_job_attempts(
    job_id: UUID,
    service: JobServiceDependency,
) -> tuple[JobAttemptRecord, ...]:
    try:
        return tuple(service.list_attempts(job_id))
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/job-attempts/{attempt_id}/logs", response_model=tuple[JobLogRecord, ...])
def list_job_logs(
    attempt_id: UUID,
    service: JobServiceDependency,
) -> tuple[JobLogRecord, ...]:
    try:
        return tuple(service.list_logs(attempt_id))
    except (JobServiceError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


def _raise_http(exc: JobServiceError | ArtifactStorageError | ValidationError) -> NoReturn:
    if isinstance(exc, JobNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (DuplicateJob, InvalidJobState, LeaseLost)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    ) from exc
