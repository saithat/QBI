"""Public discovery ingestion and crawl-frontier inspection API."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from hiveblot_contracts import (
    CrawlFetchAttemptRecord,
    CrawlFetchTaskRecord,
    CrawlFrontierFilters,
    CrawlFrontierPage,
    CrawlFrontierRecord,
    CrawlFrontierStatus,
    CrawlMetricsSnapshot,
    DiscoveryAccessStatus,
    DiscoveryEntityKind,
    DiscoveryIngestionResult,
)
from hiveblot_crawler import (
    DiscoveryError,
    DuplicateDiscoveryBatch,
    FetchQueueService,
    FetchTaskNotFound,
    FrontierConcurrencyConflict,
    FrontierNotFound,
    FrontierService,
    InvalidFrontierState,
)
from hiveblot_storage import ArtifactNotFound, ArtifactStorageError
from pydantic import ValidationError

from .discovery_dependencies import get_fetch_queue_service, get_frontier_service
from .discovery_schemas import (
    IngestDiscoveryBatchRequest,
    RecordFrontierAcquisitionRequest,
    RetryFrontierRequest,
    UpdateFrontierScheduleRequest,
)

router = APIRouter(prefix="/api/v1", tags=["public discovery"])
FrontierServiceDependency = Annotated[FrontierService, Depends(get_frontier_service)]
FetchQueueServiceDependency = Annotated[FetchQueueService, Depends(get_fetch_queue_service)]


@router.post(
    "/discovery-batches",
    response_model=DiscoveryIngestionResult,
    status_code=status.HTTP_201_CREATED,
)
def ingest_discovery_batch(
    request: IngestDiscoveryBatchRequest,
    service: FrontierServiceDependency,
) -> DiscoveryIngestionResult:
    try:
        return service.ingest(request.batch)
    except (DiscoveryError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/discovery-frontier", response_model=CrawlFrontierPage)
def browse_discovery_frontier(
    service: FrontierServiceDependency,
    frontier_status: Annotated[CrawlFrontierStatus | None, Query(alias="status")] = None,
    entity_kind: DiscoveryEntityKind | None = None,
    source_name: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    access_status: DiscoveryAccessStatus | None = None,
    missing_artifact: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CrawlFrontierPage:
    filters = CrawlFrontierFilters(
        status=frontier_status,
        entity_kind=entity_kind,
        source_name=source_name,
        access_status=access_status,
        missing_artifact=missing_artifact,
    )
    try:
        return service.browse(filters, limit=limit, offset=offset)
    except (DiscoveryError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)


@router.get("/discovery-frontier/{frontier_id}", response_model=CrawlFrontierRecord)
def get_discovery_frontier_record(
    frontier_id: UUID,
    service: FrontierServiceDependency,
) -> CrawlFrontierRecord:
    try:
        return service.get(frontier_id)
    except (DiscoveryError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.patch("/discovery-frontier/{frontier_id}/schedule", response_model=CrawlFrontierRecord)
def update_discovery_schedule(
    frontier_id: UUID,
    request: UpdateFrontierScheduleRequest,
    service: FrontierServiceDependency,
) -> CrawlFrontierRecord:
    try:
        return service.update_schedule(
            frontier_id,
            expected_version=request.expected_version,
            priority=request.priority,
            next_eligible_fetch_at=request.next_eligible_fetch_at,
        )
    except (DiscoveryError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.post("/discovery-frontier/{frontier_id}/retry", response_model=CrawlFrontierRecord)
def retry_discovery_frontier_record(
    frontier_id: UUID,
    request: RetryFrontierRequest,
    service: FrontierServiceDependency,
) -> CrawlFrontierRecord:
    try:
        return service.retry(
            frontier_id,
            expected_version=request.expected_version,
            next_eligible_fetch_at=request.next_eligible_fetch_at,
            rationale=request.rationale,
        )
    except (DiscoveryError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.post(
    "/discovery-frontier/{frontier_id}/acquisitions",
    response_model=CrawlFrontierRecord,
)
def record_discovery_acquisition(
    frontier_id: UUID,
    request: RecordFrontierAcquisitionRequest,
    service: FrontierServiceDependency,
) -> CrawlFrontierRecord:
    try:
        return service.record_acquisition(
            frontier_id,
            expected_version=request.expected_version,
            artifact_id=request.artifact_id,
            trace_id=request.trace_id,
        )
    except (DiscoveryError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/crawl-fetch-tasks/{task_id}", response_model=CrawlFetchTaskRecord)
def get_crawl_fetch_task(
    task_id: UUID,
    service: FetchQueueServiceDependency,
) -> CrawlFetchTaskRecord:
    try:
        return service.get_task(task_id)
    except (DiscoveryError, ValidationError) as exc:
        _raise_http(exc)


@router.get(
    "/crawl-fetch-tasks/{task_id}/attempts",
    response_model=tuple[CrawlFetchAttemptRecord, ...],
)
def list_crawl_fetch_attempts(
    task_id: UUID,
    service: FetchQueueServiceDependency,
) -> Sequence[CrawlFetchAttemptRecord]:
    try:
        return service.list_attempts(task_id)
    except (DiscoveryError, ValidationError) as exc:
        _raise_http(exc)


@router.get("/crawl/metrics", response_model=CrawlMetricsSnapshot)
def get_crawl_metrics(service: FetchQueueServiceDependency) -> CrawlMetricsSnapshot:
    try:
        return service.metrics()
    except (DiscoveryError, ValidationError) as exc:
        _raise_http(exc)


def _raise_http(
    exc: DiscoveryError | ArtifactStorageError | ValidationError | ValueError,
) -> NoReturn:
    if isinstance(exc, (FrontierNotFound, FetchTaskNotFound, ArtifactNotFound)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(
        exc,
        (DuplicateDiscoveryBatch, FrontierConcurrencyConflict, InvalidFrontierState),
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    ) from exc
