"""Durable fetch-queue lifecycle and long-lived worker orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import Protocol
from uuid import UUID

from hiveblot_contracts import (
    ArtifactReference,
    ArtifactRelationship,
    ArtifactVisibility,
    CrawlFetchAttemptRecord,
    CrawlFetchError,
    CrawlFetchLease,
    CrawlFetchTaskRecord,
    CrawlMetricsSnapshot,
)
from hiveblot_storage import ArtifactStorageError, InvalidArtifact, PublishedArtifact

from .errors import FetchValidationError


@dataclass(frozen=True, slots=True)
class FetchedPayload:
    """Bounded bytes returned inside one worker process before artifact publication."""

    request_url: str
    final_url: str
    http_status: int
    media_type: str | None
    content: bytes
    etag: str | None
    last_modified: str | None
    started_at: datetime
    completed_at: datetime


class FetchFailure(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        started_at: datetime,
        completed_at: datetime,
        final_url: str | None = None,
        http_status: int | None = None,
        retry_after: datetime | None = None,
        prohibited: bool = False,
    ) -> None:
        super().__init__(message)
        self.error = CrawlFetchError(code=code, message=message, retryable=retryable)
        self.started_at = started_at
        self.completed_at = completed_at
        self.final_url = final_url
        self.http_status = http_status
        self.retry_after = retry_after
        self.prohibited = prohibited


class FetchQueueRepository(Protocol):
    def enqueue_eligible(
        self,
        *,
        enqueued_at: datetime,
        limit: int,
        max_attempts: int,
        minimum_interval_milliseconds: int,
        maximum_concurrency: int,
    ) -> int: ...

    def reap_expired_leases(self, *, expired_at: datetime) -> Sequence[CrawlFetchTaskRecord]: ...

    def lease_next(
        self,
        *,
        worker_id: str,
        leased_at: datetime,
        expires_at: datetime,
    ) -> CrawlFetchLease | None: ...

    def heartbeat(
        self,
        lease_token: UUID,
        *,
        heartbeat_at: datetime,
        expires_at: datetime,
    ) -> CrawlFetchLease: ...

    def complete_success(
        self,
        lease: CrawlFetchLease,
        *,
        artifact: ArtifactReference,
        artifact_deduplicated: bool,
        request_url: str,
        final_url: str,
        http_status: int,
        media_type: str,
        response_sha256: str,
        bytes_downloaded: int,
        latency_milliseconds: int,
        etag: str | None,
        last_modified: str | None,
        started_at: datetime,
        completed_at: datetime,
    ) -> CrawlFetchTaskRecord: ...

    def complete_not_modified(
        self,
        lease: CrawlFetchLease,
        *,
        request_url: str,
        final_url: str,
        http_status: int,
        latency_milliseconds: int,
        etag: str | None,
        last_modified: str | None,
        started_at: datetime,
        completed_at: datetime,
    ) -> CrawlFetchTaskRecord: ...

    def complete_failure(
        self,
        lease: CrawlFetchLease,
        *,
        error: CrawlFetchError,
        request_url: str,
        final_url: str | None,
        http_status: int | None,
        retry_after: datetime | None,
        prohibited: bool,
        started_at: datetime,
        completed_at: datetime,
    ) -> CrawlFetchTaskRecord: ...

    def get_task(self, task_id: UUID) -> CrawlFetchTaskRecord | None: ...

    def list_attempts(self, task_id: UUID) -> Sequence[CrawlFetchAttemptRecord]: ...

    def metrics(self, *, measured_at: datetime) -> CrawlMetricsSnapshot: ...


class FetchClient(Protocol):
    def fetch(self, lease: CrawlFetchLease) -> FetchedPayload: ...


class SourcePayloadPublisher(Protocol):
    def publish_source_payload(
        self,
        *,
        original_filename: str,
        declared_media_type: str,
        content: bytes,
        source_uri: str,
        visibility: ArtifactVisibility,
        organization_id: UUID | None,
        relationships: tuple[ArtifactRelationship, ...],
        actor_id: UUID | None,
    ) -> PublishedArtifact: ...


class FetchQueueService:
    def __init__(
        self,
        repository: FetchQueueRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        max_attempts: int = 5,
        minimum_interval_milliseconds: int = 1000,
        maximum_concurrency: int = 2,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_attempts = max_attempts
        self._minimum_interval_milliseconds = minimum_interval_milliseconds
        self._maximum_concurrency = maximum_concurrency

    def enqueue_eligible(self, *, limit: int = 500) -> int:
        if not 1 <= limit <= 10_000:
            raise ValueError("fetch enqueue limit must be between 1 and 10000")
        return self._repository.enqueue_eligible(
            enqueued_at=self._clock(),
            limit=limit,
            max_attempts=self._max_attempts,
            minimum_interval_milliseconds=self._minimum_interval_milliseconds,
            maximum_concurrency=self._maximum_concurrency,
        )

    def lease_next(self, *, worker_id: str, lease_seconds: int) -> CrawlFetchLease | None:
        if not 5 <= lease_seconds <= 3600:
            raise ValueError("fetch lease_seconds must be between 5 and 3600")
        now = self._clock()
        self._repository.reap_expired_leases(expired_at=now)
        self.enqueue_eligible()
        return self._repository.lease_next(
            worker_id=worker_id,
            leased_at=now,
            expires_at=now + timedelta(seconds=lease_seconds),
        )

    def heartbeat(self, lease_token: UUID, *, lease_seconds: int) -> CrawlFetchLease:
        if not 5 <= lease_seconds <= 3600:
            raise ValueError("fetch lease_seconds must be between 5 and 3600")
        now = self._clock()
        return self._repository.heartbeat(
            lease_token,
            heartbeat_at=now,
            expires_at=now + timedelta(seconds=lease_seconds),
        )

    def get_task(self, task_id: UUID) -> CrawlFetchTaskRecord:
        record = self._repository.get_task(task_id)
        if record is None:
            from .errors import FetchTaskNotFound

            raise FetchTaskNotFound(f"fetch task {task_id} does not exist")
        return record

    def list_attempts(self, task_id: UUID) -> Sequence[CrawlFetchAttemptRecord]:
        self.get_task(task_id)
        return self._repository.list_attempts(task_id)

    def metrics(self) -> CrawlMetricsSnapshot:
        return self._repository.metrics(measured_at=self._clock())


class FetchWorker:
    def __init__(
        self,
        queue: FetchQueueService,
        repository: FetchQueueRepository,
        client: FetchClient,
        artifacts: SourcePayloadPublisher,
        *,
        worker_id: str,
        lease_seconds: int,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        heartbeat_interval = (
            lease_seconds / 3 if heartbeat_interval_seconds is None else heartbeat_interval_seconds
        )
        if not 0 < heartbeat_interval < lease_seconds:
            raise ValueError("fetch heartbeat interval must be shorter than the lease")
        self._queue = queue
        self._repository = repository
        self._client = client
        self._artifacts = artifacts
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._heartbeat_interval_seconds = heartbeat_interval

    def run_once(self) -> CrawlFetchTaskRecord | None:
        lease = self._queue.lease_next(
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
        )
        if lease is None:
            return None
        heartbeat = _LeaseHeartbeater(
            self._queue,
            lease_token=lease.lease_token,
            lease_seconds=self._lease_seconds,
            interval_seconds=self._heartbeat_interval_seconds,
        )
        heartbeat.start()
        try:
            payload = self._client.fetch(lease)
            latency = _duration_milliseconds(payload.started_at, payload.completed_at)
            if payload.http_status == 304:
                heartbeat.stop()
                return self._repository.complete_not_modified(
                    lease,
                    request_url=payload.request_url,
                    final_url=payload.final_url,
                    http_status=payload.http_status,
                    latency_milliseconds=latency,
                    etag=payload.etag,
                    last_modified=payload.last_modified,
                    started_at=payload.started_at,
                    completed_at=payload.completed_at,
                )
            if payload.media_type is None:
                raise FetchFailure(
                    "missing_media_type",
                    "successful source response omitted Content-Type",
                    retryable=False,
                    final_url=payload.final_url,
                    http_status=payload.http_status,
                    started_at=payload.started_at,
                    completed_at=payload.completed_at,
                )
            if payload.media_type not in lease.expected_media_types:
                raise FetchFailure(
                    "unexpected_media_type",
                    f"source returned {payload.media_type!r}; expected "
                    + ", ".join(lease.expected_media_types),
                    retryable=False,
                    final_url=payload.final_url,
                    http_status=payload.http_status,
                    started_at=payload.started_at,
                    completed_at=payload.completed_at,
                )
            digest = hashlib.sha256(payload.content).hexdigest()
            source_uri = (
                payload.final_url
                if len(payload.final_url) <= 2048
                else f"urn:hiveblot:fetched-response:sha256:{digest}"
            )
            published = self._artifacts.publish_source_payload(
                original_filename=f"crawl-response-{digest}",
                declared_media_type=payload.media_type,
                content=payload.content,
                source_uri=source_uri,
                visibility=ArtifactVisibility.PUBLIC,
                organization_id=None,
                relationships=(),
                actor_id=None,
            )
            artifact = ArtifactReference(
                artifact_id=published.artifact.artifact_id,
                sha256=published.artifact.sha256,
                media_type=published.artifact.media_type,
                byte_size=published.artifact.byte_size,
            )
            if artifact.sha256 != digest or artifact.byte_size != len(payload.content):
                raise FetchValidationError(
                    "published artifact does not match the downloaded response bytes"
                )
            heartbeat.stop()
            return self._repository.complete_success(
                lease,
                artifact=artifact,
                artifact_deduplicated=published.deduplicated,
                request_url=payload.request_url,
                final_url=payload.final_url,
                http_status=payload.http_status,
                media_type=payload.media_type,
                response_sha256=digest,
                bytes_downloaded=len(payload.content),
                latency_milliseconds=latency,
                etag=payload.etag,
                last_modified=payload.last_modified,
                started_at=payload.started_at,
                completed_at=payload.completed_at,
            )
        except FetchFailure as exc:
            heartbeat.stop()
            return self._repository.complete_failure(
                lease,
                error=exc.error,
                request_url=lease.canonical_url,
                final_url=exc.final_url,
                http_status=exc.http_status,
                retry_after=exc.retry_after,
                prohibited=exc.prohibited,
                started_at=exc.started_at,
                completed_at=exc.completed_at,
            )
        except (ArtifactStorageError, FetchValidationError) as exc:
            heartbeat.stop()
            completed_at = datetime.now(UTC)
            validation_failure = isinstance(exc, (FetchValidationError, InvalidArtifact))
            return self._repository.complete_failure(
                lease,
                error=CrawlFetchError(
                    code=(
                        "fetched_artifact_validation_failed"
                        if validation_failure
                        else "artifact_publication_failed"
                    ),
                    message=str(exc)[:4000] or "fetched artifact publication failed",
                    retryable=not validation_failure,
                ),
                request_url=lease.canonical_url,
                final_url=None,
                http_status=None,
                retry_after=None,
                prohibited=False,
                started_at=lease.leased_at,
                completed_at=completed_at,
            )
        finally:
            heartbeat.stop(raise_on_failure=False)


class _LeaseHeartbeater:
    """Refresh one lease while HTTP and artifact publication remain in flight."""

    def __init__(
        self,
        queue: FetchQueueService,
        *,
        lease_token: UUID,
        lease_seconds: int,
        interval_seconds: float,
    ) -> None:
        self._queue = queue
        self._lease_token = lease_token
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds
        self._stop_event = Event()
        self._thread = Thread(
            target=self._run,
            name=f"fetch-heartbeat-{lease_token}",
            daemon=True,
        )
        self._started = False
        self._stopped = False
        self._failure: Exception | None = None

    def start(self) -> None:
        self._thread.start()
        self._started = True

    def stop(self, *, raise_on_failure: bool = True) -> None:
        if self._started and not self._stopped:
            self._stop_event.set()
            self._thread.join()
            self._stopped = True
        if raise_on_failure and self._failure is not None:
            raise self._failure

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self._queue.heartbeat(
                    self._lease_token,
                    lease_seconds=self._lease_seconds,
                )
            except Exception as exc:  # noqa: BLE001 - preserve the lease failure for the worker
                self._failure = exc
                return


def _duration_milliseconds(started_at: datetime, completed_at: datetime) -> int:
    return max(0, int((completed_at - started_at).total_seconds() * 1000))
