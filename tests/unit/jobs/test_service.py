from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from hiveblot_contracts import (
    ArtifactReference,
    ArtifactVisibility,
    CancelledJobResult,
    FailedJobResult,
    JobError,
    JobStatus,
    NamedArtifactReference,
    SucceededJobResult,
)
from hiveblot_job_service import InvalidJobState, JobService
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    SourceAdapterRegistry,
)

from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore
from tests.fakes.job_service import InMemoryJobRepository
from workers.jobs.specifications import legacy_normalization_job


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def test_retryable_failure_is_released_with_backoff_and_preserves_attempt() -> None:
    clock = MutableClock(datetime(2026, 8, 2, 12, tzinfo=UTC))
    artifacts, reference = _artifact_fixture(clock)
    repository = InMemoryJobRepository()
    service = JobService(repository, artifacts, clock=clock)
    specification = legacy_normalization_job(
        reference,
        image="hiveblot:test",
        idempotency_key="retry-case",
        submitted_at=clock(),
    )

    submitted = service.submit(specification)
    assert service.submit(specification) == submitted
    lease = service.lease_next(worker_id="worker-a", executor_name="local-docker", lease_seconds=30)
    assert lease is not None
    service.mark_running(lease.lease_token)
    result = FailedJobResult(
        job_id=lease.job_id,
        attempt=lease.attempt,
        trace_id=lease.specification.trace_id,
        started_at=clock(),
        completed_at=clock(),
        error=JobError(code="container_exit", message="temporary", retryable=True),
    )

    pending = service.publish_result(lease.lease_token, result)

    assert pending.status is JobStatus.PENDING
    assert pending.result is None
    assert (
        service.lease_next(worker_id="worker-b", executor_name="local-docker", lease_seconds=30)
        is None
    )
    clock.advance(specification.retry_initial_backoff_seconds)
    second = service.lease_next(
        worker_id="worker-b", executor_name="local-docker", lease_seconds=30
    )
    assert second is not None
    assert second.attempt == 2
    assert service.list_attempts(specification.job_id)[0].result == result


def test_cancellation_blocks_success_and_accepts_cancelled_result() -> None:
    clock = MutableClock(datetime(2026, 8, 2, 12, tzinfo=UTC))
    artifacts, reference = _artifact_fixture(clock)
    service = JobService(InMemoryJobRepository(), artifacts, clock=clock)
    specification = legacy_normalization_job(
        reference,
        image="hiveblot:test",
        idempotency_key="cancel-case",
        submitted_at=clock(),
    )
    service.submit(specification)
    lease = service.lease_next(worker_id="worker-a", executor_name="local-docker", lease_seconds=30)
    assert lease is not None
    service.mark_running(lease.lease_token)
    service.cancel(lease.job_id, reason="reviewer requested cancellation")
    success = SucceededJobResult(
        job_id=lease.job_id,
        attempt=lease.attempt,
        trace_id=lease.specification.trace_id,
        started_at=clock(),
        completed_at=clock(),
        outputs=(NamedArtifactReference(name="records", artifact=reference),),
    )

    with pytest.raises(InvalidJobState, match="cancelled job cannot publish success"):
        service.publish_result(lease.lease_token, success)

    cancelled_result = CancelledJobResult(
        job_id=lease.job_id,
        attempt=lease.attempt,
        trace_id=lease.specification.trace_id,
        started_at=clock(),
        completed_at=clock(),
        reason="reviewer requested cancellation",
    )
    cancelled = service.publish_result(lease.lease_token, cancelled_result)
    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.result == cancelled_result


def test_expired_leases_retry_then_dead_letter() -> None:
    clock = MutableClock(datetime(2026, 8, 2, 12, tzinfo=UTC))
    artifacts, reference = _artifact_fixture(clock)
    service = JobService(InMemoryJobRepository(), artifacts, clock=clock)
    specification = legacy_normalization_job(
        reference,
        image="hiveblot:test",
        idempotency_key="expired-case",
        submitted_at=clock(),
    ).model_copy(update={"max_attempts": 2})
    service.submit(specification)

    first = service.lease_next(worker_id="worker-a", executor_name="local-docker", lease_seconds=5)
    assert first is not None
    clock.advance(5)
    assert service.reap_expired_leases()[0].status is JobStatus.PENDING
    second = service.lease_next(worker_id="worker-b", executor_name="local-docker", lease_seconds=5)
    assert second is not None
    clock.advance(5)
    assert service.reap_expired_leases()[0].status is JobStatus.DEAD_LETTER
    assert [item.status.value for item in service.list_attempts(specification.job_id)] == [
        "lease_expired",
        "lease_expired",
    ]


def test_worker_cannot_publish_cancellation_without_a_request() -> None:
    clock = MutableClock(datetime(2026, 8, 2, 12, tzinfo=UTC))
    artifacts, reference = _artifact_fixture(clock)
    service = JobService(InMemoryJobRepository(), artifacts, clock=clock)
    specification = legacy_normalization_job(
        reference,
        image="hiveblot:test",
        idempotency_key="unsolicited-cancel",
        submitted_at=clock(),
    )
    service.submit(specification)
    lease = service.lease_next(worker_id="worker-a", executor_name="local-docker", lease_seconds=30)
    assert lease is not None
    service.mark_running(lease.lease_token)
    result = CancelledJobResult(
        job_id=lease.job_id,
        attempt=lease.attempt,
        trace_id=lease.specification.trace_id,
        started_at=clock(),
        completed_at=clock(),
        reason="unexpected",
    )

    with pytest.raises(InvalidJobState, match="without a cancellation request"):
        service.publish_result(lease.lease_token, result)


def _artifact_fixture(
    clock: MutableClock,
) -> tuple[ArtifactService, ArtifactReference]:
    repository = InMemoryArtifactRepository()
    store = InMemoryObjectStore()
    service = ArtifactService(
        repository=repository,
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=1_000_000,
        upload_url_seconds=3600,
        download_url_seconds=900,
        clock=clock,
    )
    content = b'{"fixture":true}'
    upload = service.begin_multipart_upload(
        original_filename="model-output.json",
        declared_media_type="application/json",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:test:model-output",
        relationships=(),
        actor_id=None,
    )
    session = repository.uploads[upload.upload_id]
    assert session.backend_upload_id is not None
    etag = store.put_part(session.backend_upload_id, 1, content)
    artifact = service.complete_multipart_upload(
        upload.upload_id,
        (CompletedPart(part_number=1, etag=etag),),
        actor_id=None,
    ).artifact
    return service, ArtifactReference(
        artifact_id=artifact.artifact_id,
        sha256=artifact.sha256,
        media_type=artifact.media_type,
        byte_size=artifact.byte_size,
    )
