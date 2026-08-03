from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from hiveblot_contracts import (
    ArtifactReference,
    CrawlFetchAttemptOutcome,
    CrawlFetchAttemptRecord,
    CrawlFetchLease,
    CrawlFetchTaskRecord,
    CrawlFetchTaskStatus,
    DiscoveryAcquisitionMethod,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
TRACE_ID = UUID("00000000-0000-0000-0000-000000000123")


def _lease() -> CrawlFetchLease:
    return CrawlFetchLease(
        task_id=uuid4(),
        frontier_id=uuid4(),
        attempt_id=uuid4(),
        attempt=1,
        worker_id="fetch-worker-1",
        lease_token=uuid4(),
        canonical_url="https://example.test/source.pdf",
        domain="example.test",
        acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
        expected_media_types=("application/pdf",),
        trace_id=TRACE_ID,
        leased_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
    )


def test_fetch_lease_is_strict_frozen_and_versioned() -> None:
    lease = _lease()
    assert lease.schema_version == "1.0"
    with pytest.raises(ValidationError):
        CrawlFetchLease.model_validate({**lease.model_dump(), "unknown": True}, strict=True)
    with pytest.raises(ValidationError):
        lease.attempt = 2  # type: ignore[misc]


def test_fetch_task_requires_lease_and_terminal_result_shapes() -> None:
    lease = _lease()
    with pytest.raises(ValidationError, match="only leased"):
        CrawlFetchTaskRecord(
            task_id=lease.task_id,
            frontier_id=lease.frontier_id,
            canonical_url=lease.canonical_url,
            domain=lease.domain,
            acquisition_method=lease.acquisition_method,
            expected_media_types=lease.expected_media_types,
            status=CrawlFetchTaskStatus.PENDING,
            priority=100,
            attempt_count=1,
            max_attempts=3,
            next_eligible_at=NOW,
            trace_id=TRACE_ID,
            lease=lease,
            created_at=NOW,
            updated_at=NOW,
        )
    with pytest.raises(ValidationError, match="result artifact"):
        CrawlFetchTaskRecord(
            task_id=lease.task_id,
            frontier_id=lease.frontier_id,
            canonical_url=lease.canonical_url,
            domain=lease.domain,
            acquisition_method=lease.acquisition_method,
            expected_media_types=lease.expected_media_types,
            status=CrawlFetchTaskStatus.SUCCEEDED,
            priority=100,
            attempt_count=1,
            max_attempts=3,
            next_eligible_at=NOW,
            trace_id=TRACE_ID,
            created_at=NOW,
            updated_at=NOW,
            completed_at=NOW,
        )


def test_fetch_attempt_artifact_must_match_exact_response() -> None:
    lease = _lease()
    artifact = ArtifactReference(
        artifact_id=uuid4(),
        sha256="a" * 64,
        media_type="application/pdf",
        byte_size=4,
    )
    with pytest.raises(ValidationError, match="hash"):
        CrawlFetchAttemptRecord(
            attempt_id=lease.attempt_id,
            task_id=lease.task_id,
            frontier_id=lease.frontier_id,
            attempt=1,
            worker_id=lease.worker_id,
            trace_id=TRACE_ID,
            outcome=CrawlFetchAttemptOutcome.SUCCEEDED,
            request_url=lease.canonical_url,
            final_url=lease.canonical_url,
            http_status=200,
            response_media_type="application/pdf",
            response_sha256="b" * 64,
            bytes_downloaded=4,
            latency_milliseconds=10,
            artifact=artifact,
            artifact_deduplicated=False,
            leased_at=NOW,
            started_at=NOW,
            completed_at=NOW,
        )
