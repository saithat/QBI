"""Opt-in PostgreSQL acceptance test for PRD-016 distributed fetch state."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from hiveblot_contracts import (
    ArtifactReference,
    CrawlFetchError,
    CrawlFetchTaskStatus,
    DiscoveryAccessStatus,
    DiscoveryAcquisitionMethod,
    DiscoveryBatch,
    DiscoveryEntityKind,
    DiscoveryEvidence,
    DiscoveryLicense,
    DiscoveryLicenseStatus,
    DiscoveryQuery,
    DiscoveryRecord,
    DiscoveryRobotsStatus,
    ToolIdentifier,
)
from hiveblot_crawler import (
    FetchQueueService,
    FrontierService,
    PostgresFetchRepository,
    PostgresFrontierRepository,
)

from hiveblot import db
from hiveblot.settings import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_FETCH") != "1",
    reason="set HIVEBLOT_RUN_LIVE_FETCH=1 with local PostgreSQL",
)

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def test_fetch_queue_leases_rate_limits_completion_outbox_and_conditional_recrawl() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    frontier_repository = PostgresFrontierRepository(settings.database_url)
    fetch_repository = PostgresFetchRepository(
        settings.database_url,
        default_minimum_interval_milliseconds=100,
        default_maximum_concurrency=1,
    )
    trace_id = uuid4()
    batch_id = uuid4()
    evidence_artifact = _insert_artifact(
        settings.database_url,
        media_type="application/xml",
        byte_size=1,
        storage_prefix="fetch-evidence",
    )
    fetched_artifact = _insert_artifact(
        settings.database_url,
        media_type="application/pdf",
        byte_size=12,
        storage_prefix="fetch-result",
    )
    identity = f"fetch-live:{uuid4().hex}"
    record = DiscoveryRecord(
        identity_key=identity,
        source_record_id=identity,
        entity_kind=DiscoveryEntityKind.PAPER,
        canonical_url=f"https://fetch-live.example/{uuid4().hex}.pdf",
        acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
        discovered_at=NOW,
        next_eligible_fetch_at=NOW,
        robots_status=DiscoveryRobotsStatus.NOT_APPLICABLE,
        expected_media_types=("application/pdf",),
        license=DiscoveryLicense(
            status=DiscoveryLicenseStatus.UNVERIFIED,
            statement="live queue fixture",
        ),
        access_status=DiscoveryAccessStatus.ALLOWED,
        trace_id=trace_id,
    )
    batch = DiscoveryBatch(
        batch_id=batch_id,
        source=ToolIdentifier(name="fetch-live", version="1.0.0"),
        query=DiscoveryQuery(maximum_pages=1),
        trace_id=trace_id,
        started_at=NOW,
        completed_at=NOW,
        records=(record,),
        evidence=(
            DiscoveryEvidence(
                request_url="https://fetch-live.example/discovery",
                response_artifact=evidence_artifact,
                response_sha256=evidence_artifact.sha256,
                response_media_type=evidence_artifact.media_type,
                http_status=200,
                fetched_at=NOW,
            ),
        ),
    )
    frontier_id: UUID | None = None
    try:
        frontier = FrontierService(frontier_repository).ingest(batch).records[0]
        frontier_id = frontier.frontier_id
        queue = FetchQueueService(
            fetch_repository,
            clock=lambda: NOW,
            max_attempts=3,
            minimum_interval_milliseconds=100,
            maximum_concurrency=1,
        )
        assert queue.enqueue_eligible() == 1
        lease = queue.lease_next(worker_id="worker-a", lease_seconds=60)
        assert lease is not None
        assert queue.lease_next(worker_id="worker-b", lease_seconds=60) is None

        first_permit, _ = fetch_repository.try_acquire_domain_permit(
            domain=lease.domain,
            worker_id="worker-a",
            acquired_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
        )
        assert first_permit is not None
        second_permit, retry_at = fetch_repository.try_acquire_domain_permit(
            domain=lease.domain,
            worker_id="worker-b",
            acquired_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
        )
        assert second_permit is None
        assert retry_at > NOW
        fetch_repository.release_domain_permit(first_permit, released_at=NOW)

        def acquire(worker_id: str):
            return PostgresFetchRepository(
                settings.database_url,
                default_minimum_interval_milliseconds=100,
                default_maximum_concurrency=1,
            ).try_acquire_domain_permit(
                domain=lease.domain,
                worker_id=worker_id,
                acquired_at=NOW + timedelta(seconds=1),
                expires_at=NOW + timedelta(seconds=31),
            )[0]

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent_permits = tuple(executor.map(acquire, ("worker-b", "worker-c")))
        granted = [permit for permit in concurrent_permits if permit is not None]
        assert len(granted) == 1
        fetch_repository.release_domain_permit(granted[0], released_at=NOW + timedelta(seconds=1))

        completed = fetch_repository.complete_success(
            lease,
            artifact=fetched_artifact,
            artifact_deduplicated=False,
            request_url=lease.canonical_url,
            final_url=lease.canonical_url,
            http_status=200,
            media_type="application/pdf",
            response_sha256=fetched_artifact.sha256,
            bytes_downloaded=fetched_artifact.byte_size,
            latency_milliseconds=1000,
            etag='"fixture-v1"',
            last_modified="Sun, 02 Aug 2026 12:00:00 GMT",
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=1),
        )
        assert completed.status is CrawlFetchTaskStatus.SUCCEEDED
        assert completed.result_artifact == fetched_artifact
        assert len(queue.list_attempts(completed.task_id)) == 1
        assert queue.metrics().completed_fetches >= 1
        with psycopg.connect(settings.database_url) as connection:
            downstream_count = connection.execute(
                "SELECT COUNT(*) FROM crawl_downstream_tasks WHERE fetch_task_id = %s",
                (completed.task_id,),
            ).fetchone()
        assert downstream_count is not None and downstream_count[0] == 1

        current = frontier_repository.get(frontier.frontier_id)
        assert current is not None
        rescheduled = FrontierService(
            frontier_repository,
            clock=lambda: NOW + timedelta(seconds=2),
        ).update_schedule(
            frontier.frontier_id,
            expected_version=current.version,
            priority=current.priority,
            next_eligible_fetch_at=NOW + timedelta(seconds=2),
        )
        assert rescheduled.status.value == "pending"
        recrawl_queue = FetchQueueService(
            fetch_repository,
            clock=lambda: NOW + timedelta(seconds=2),
            max_attempts=3,
            minimum_interval_milliseconds=100,
            maximum_concurrency=1,
        )
        assert recrawl_queue.enqueue_eligible() == 1
        recrawl = recrawl_queue.lease_next(worker_id="worker-c", lease_seconds=60)
        assert recrawl is not None
        assert recrawl.etag == '"fixture-v1"'
        not_modified = fetch_repository.complete_not_modified(
            recrawl,
            request_url=recrawl.canonical_url,
            final_url=recrawl.canonical_url,
            http_status=304,
            latency_milliseconds=10,
            etag='"fixture-v1"',
            last_modified=recrawl.last_modified,
            started_at=NOW + timedelta(seconds=2),
            completed_at=NOW + timedelta(seconds=3),
        )
        assert not_modified.status is CrawlFetchTaskStatus.NOT_MODIFIED

        current = frontier_repository.get(frontier.frontier_id)
        assert current is not None
        FrontierService(
            frontier_repository,
            clock=lambda: NOW + timedelta(seconds=4),
        ).update_schedule(
            frontier.frontier_id,
            expected_version=current.version,
            priority=current.priority,
            next_eligible_fetch_at=NOW + timedelta(seconds=4),
        )
        recovery_queue = FetchQueueService(
            fetch_repository,
            clock=lambda: NOW + timedelta(seconds=4),
            max_attempts=3,
            minimum_interval_milliseconds=100,
            maximum_concurrency=1,
        )
        assert recovery_queue.enqueue_eligible() == 1
        abandoned = recovery_queue.lease_next(worker_id="worker-d", lease_seconds=5)
        assert abandoned is not None
        reaped = fetch_repository.reap_expired_leases(expired_at=NOW + timedelta(seconds=10))
        assert reaped[0].status is CrawlFetchTaskStatus.RETRY_WAIT
        retry_lease = fetch_repository.lease_next(
            worker_id="worker-e",
            leased_at=NOW + timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=70),
        )
        assert retry_lease is not None and retry_lease.attempt == 2
        retry_wait = fetch_repository.complete_failure(
            retry_lease,
            error=CrawlFetchError(
                code="http_429",
                message="simulated source rate limit",
                retryable=True,
            ),
            request_url=retry_lease.canonical_url,
            final_url=retry_lease.canonical_url,
            http_status=429,
            retry_after=NOW + timedelta(seconds=30),
            prohibited=False,
            started_at=NOW + timedelta(seconds=10),
            completed_at=NOW + timedelta(seconds=11),
        )
        assert retry_wait.status is CrawlFetchTaskStatus.RETRY_WAIT
        assert retry_wait.next_eligible_at == NOW + timedelta(seconds=30)
        assert (
            fetch_repository.lease_next(
                worker_id="worker-f",
                leased_at=NOW + timedelta(seconds=20),
                expires_at=NOW + timedelta(seconds=80),
            )
            is None
        )
        final_lease = fetch_repository.lease_next(
            worker_id="worker-f",
            leased_at=NOW + timedelta(seconds=30),
            expires_at=NOW + timedelta(seconds=90),
        )
        assert final_lease is not None and final_lease.attempt == 3
        dead_letter = fetch_repository.complete_failure(
            final_lease,
            error=CrawlFetchError(
                code="invalid_content",
                message="simulated permanent content failure",
                retryable=False,
            ),
            request_url=final_lease.canonical_url,
            final_url=final_lease.canonical_url,
            http_status=200,
            retry_after=None,
            prohibited=False,
            started_at=NOW + timedelta(seconds=30),
            completed_at=NOW + timedelta(seconds=31),
        )
        assert dead_letter.status is CrawlFetchTaskStatus.DEAD_LETTER
        assert len(recovery_queue.list_attempts(dead_letter.task_id)) == 3
    finally:
        _cleanup(
            settings.database_url,
            frontier_id=frontier_id,
            batch_id=batch_id,
            artifact_references=(evidence_artifact, fetched_artifact),
        )


def _insert_artifact(
    database_url: str,
    *,
    media_type: str,
    byte_size: int,
    storage_prefix: str,
) -> ArtifactReference:
    artifact_id = uuid4()
    digest = uuid4().hex + uuid4().hex
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            INSERT INTO artifact_blobs (sha256, media_type, byte_size, storage_key, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (digest, media_type, byte_size, f"{storage_prefix}/{artifact_id}", NOW),
        )
        connection.execute(
            """
            INSERT INTO artifacts (
                artifact_id, blob_sha256, original_filename, source_uri,
                acquisition_method, visibility, organization_id, created_at
            ) VALUES (%s, %s, %s, %s, 'source_adapter', 'public', NULL, %s)
            """,
            (
                artifact_id,
                digest,
                f"{artifact_id}.fixture",
                f"https://fetch-live.example/{artifact_id}",
                NOW,
            ),
        )
    return ArtifactReference(
        artifact_id=artifact_id,
        sha256=digest,
        media_type=media_type,
        byte_size=byte_size,
    )


def _cleanup(
    database_url: str,
    *,
    frontier_id: UUID | None,
    batch_id: UUID,
    artifact_references: tuple[ArtifactReference, ...],
) -> None:
    with psycopg.connect(database_url) as connection:
        if frontier_id is not None:
            connection.execute(
                "DELETE FROM crawl_downstream_tasks WHERE frontier_id = %s", (frontier_id,)
            )
            connection.execute(
                "DELETE FROM crawl_fetch_attempts WHERE frontier_id = %s", (frontier_id,)
            )
            connection.execute(
                "DELETE FROM crawl_fetch_tasks WHERE frontier_id = %s", (frontier_id,)
            )
            connection.execute(
                "DELETE FROM crawl_frontier_artifacts WHERE frontier_id = %s", (frontier_id,)
            )
            connection.execute(
                """
                DELETE FROM crawl_frontier_relationships
                WHERE frontier_id = %s OR related_frontier_id = %s
                """,
                (frontier_id, frontier_id),
            )
            connection.execute(
                "DELETE FROM discovery_observations WHERE frontier_id = %s", (frontier_id,)
            )
            connection.execute(
                "DELETE FROM crawl_frontier_events WHERE frontier_id = %s", (frontier_id,)
            )
            connection.execute(
                "DELETE FROM crawl_frontier_aliases WHERE frontier_id = %s", (frontier_id,)
            )
            connection.execute("DELETE FROM crawl_frontier WHERE frontier_id = %s", (frontier_id,))
        connection.execute("DELETE FROM discovery_run_evidence WHERE batch_id = %s", (batch_id,))
        connection.execute("DELETE FROM discovery_runs WHERE batch_id = %s", (batch_id,))
        for artifact in artifact_references:
            connection.execute(
                "DELETE FROM artifact_events WHERE artifact_id = %s", (artifact.artifact_id,)
            )
            connection.execute(
                "DELETE FROM artifacts WHERE artifact_id = %s", (artifact.artifact_id,)
            )
            connection.execute("DELETE FROM artifact_blobs WHERE sha256 = %s", (artifact.sha256,))
        connection.execute("DELETE FROM crawl_domain_permits WHERE domain = 'fetch-live.example'")
        connection.execute("DELETE FROM crawl_robots_cache WHERE domain = 'fetch-live.example'")
        connection.execute("DELETE FROM crawl_domain_policies WHERE domain = 'fetch-live.example'")
