from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactReference,
    ArtifactVisibility,
    CrawlFetchLease,
    CrawlFetchTaskRecord,
    CrawlFetchTaskStatus,
    DiscoveryAcquisitionMethod,
)
from hiveblot_crawler import FetchedPayload, FetchWorker
from hiveblot_storage import PublishedArtifact

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
TRACE_ID = UUID("00000000-0000-0000-0000-000000000123")


def test_worker_publishes_immutable_bytes_before_canonical_queue_completion() -> None:
    events: list[str] = []
    content = b"%PDF-fetch-worker-fixture"
    digest = hashlib.sha256(content).hexdigest()
    lease = CrawlFetchLease(
        task_id=uuid4(),
        frontier_id=uuid4(),
        attempt_id=uuid4(),
        attempt=1,
        worker_id="worker-1",
        lease_token=uuid4(),
        canonical_url="https://source.test/paper.pdf",
        domain="source.test",
        acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
        expected_media_types=("application/pdf",),
        trace_id=TRACE_ID,
        leased_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
    )

    class Queue:
        def __init__(self) -> None:
            self.heartbeat_seen = Event()

        def lease_next(self, *, worker_id: str, lease_seconds: int) -> CrawlFetchLease:
            assert worker_id == lease.worker_id
            assert lease_seconds == 120
            return lease

        def heartbeat(self, lease_token: UUID, *, lease_seconds: int) -> CrawlFetchLease:
            assert lease_token == lease.lease_token
            assert lease_seconds == 120
            self.heartbeat_seen.set()
            return lease.model_copy(update={"expires_at": NOW + timedelta(minutes=4)})

    queue = Queue()

    class Client:
        def fetch(self, current: CrawlFetchLease) -> FetchedPayload:
            assert current == lease
            assert queue.heartbeat_seen.wait(timeout=1)
            events.append("downloaded")
            return FetchedPayload(
                request_url=lease.canonical_url,
                final_url=lease.canonical_url,
                http_status=200,
                media_type="application/pdf",
                content=content,
                etag='"v1"',
                last_modified=None,
                started_at=NOW,
                completed_at=NOW + timedelta(seconds=1),
            )

    artifact_id = uuid4()

    class Artifacts:
        def publish_source_payload(self, **_kwargs: object) -> PublishedArtifact:
            events.append("artifact_published")
            return PublishedArtifact(
                artifact=ArtifactRecord(
                    artifact_id=artifact_id,
                    sha256=digest,
                    media_type="application/pdf",
                    byte_size=len(content),
                    original_filename="paper.pdf",
                    source_uri=lease.canonical_url,
                    acquisition_method=ArtifactAcquisitionMethod.SOURCE_ADAPTER,
                    visibility=ArtifactVisibility.PUBLIC,
                    created_at=NOW,
                ),
                deduplicated=True,
            )

    class Repository:
        def complete_success(
            self, current: CrawlFetchLease, **kwargs: object
        ) -> CrawlFetchTaskRecord:
            assert current == lease
            assert kwargs["response_sha256"] == digest
            assert kwargs["artifact_deduplicated"] is True
            artifact = kwargs["artifact"]
            assert isinstance(artifact, ArtifactReference)
            events.append("queue_completed")
            return CrawlFetchTaskRecord(
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
                result_artifact=artifact,
                etag='"v1"',
                created_at=NOW,
                updated_at=NOW + timedelta(seconds=1),
                completed_at=NOW + timedelta(seconds=1),
            )

    worker = FetchWorker(
        queue,  # type: ignore[arg-type]
        Repository(),  # type: ignore[arg-type]
        Client(),
        Artifacts(),
        worker_id="worker-1",
        lease_seconds=120,
        heartbeat_interval_seconds=0.01,
    )
    result = worker.run_once()
    assert result is not None and result.status is CrawlFetchTaskStatus.SUCCEEDED
    assert queue.heartbeat_seen.is_set()
    assert events == ["downloaded", "artifact_published", "queue_completed"]
