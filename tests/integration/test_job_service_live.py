"""Opt-in PostgreSQL, MinIO, and Docker acceptance tests for PRD-013."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from hiveblot_contracts import (
    ArtifactReference,
    ArtifactVisibility,
    CancelledJobResult,
    FailedJobResult,
    JobError,
    JobLogStream,
    JobStatus,
    NamedArtifactReference,
    SucceededJobResult,
)
from hiveblot_job_service import (
    InvalidJobState,
    JobService,
    JobWorker,
    LocalDockerExecutor,
    PostgresJobRepository,
    SubprocessDockerRunner,
)
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    PostgresArtifactRepository,
    S3ObjectStore,
    SourceAdapterRegistry,
    VerifiedArtifactReader,
)

from hiveblot import db
from hiveblot.settings import Settings
from workers.jobs.specifications import legacy_normalization_job

LIVE = os.getenv("HIVEBLOT_RUN_LIVE_JOBS") == "1"
DOCKER_LIVE = LIVE and os.getenv("HIVEBLOT_RUN_DOCKER_JOBS") == "1"


@pytest.fixture
def live_job_prefix() -> Iterator[str]:
    prefix = f"prd013-live-{uuid4()}"
    yield prefix
    settings = Settings(_env_file=None)
    with psycopg.connect(settings.database_url) as connection:
        job_ids = [
            row[0]
            for row in connection.execute(
                "SELECT job_id FROM jobs WHERE idempotency_key LIKE %s",
                (f"{prefix}%",),
            ).fetchall()
        ]
        if job_ids:
            connection.execute(
                "DELETE FROM job_outputs WHERE job_id = ANY(%s)",
                (job_ids,),
            )
            connection.execute(
                """
                DELETE FROM job_logs
                WHERE attempt_id IN (
                    SELECT attempt_id FROM job_attempts WHERE job_id = ANY(%s)
                )
                """,
                (job_ids,),
            )
            connection.execute(
                "DELETE FROM job_attempts WHERE job_id = ANY(%s)",
                (job_ids,),
            )
            connection.execute("DELETE FROM jobs WHERE job_id = ANY(%s)", (job_ids,))


@pytest.mark.skipif(not LIVE, reason="set HIVEBLOT_RUN_LIVE_JOBS=1 with PostgreSQL and MinIO")
def test_postgres_job_leases_retry_cancellation_logs_and_idempotent_results(
    live_job_prefix: str,
) -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    artifacts, _, _, source = _live_dependencies(settings)
    service = JobService(PostgresJobRepository(settings.database_url), artifacts)
    specification = legacy_normalization_job(
        source,
        image="hiveblot:test",
        idempotency_key=f"{live_job_prefix}-retry",
    ).model_copy(update={"retry_initial_backoff_seconds": 0})
    submitted = service.submit(specification)
    assert service.submit(specification) == submitted

    first = service.lease_next(worker_id="live-worker-a", executor_name="fixture", lease_seconds=30)
    assert first is not None
    running = service.mark_running(first.lease_token)
    service.append_log(
        first.attempt_id,
        sequence=1,
        stream=JobLogStream.SYSTEM,
        content="first attempt failed safely",
        truncated=False,
    )
    failed = FailedJobResult(
        job_id=first.job_id,
        attempt=first.attempt,
        trace_id=first.specification.trace_id,
        started_at=running.started_at or running.leased_at,
        completed_at=datetime.now(UTC),
        error=JobError(code="transient", message="retry", retryable=True),
    )
    assert service.publish_result(first.lease_token, failed).status is JobStatus.PENDING

    second = service.lease_next(
        worker_id="live-worker-b", executor_name="fixture", lease_seconds=30
    )
    assert second is not None
    running = service.mark_running(second.lease_token)
    service.cancel(second.job_id, reason="live cancellation assertion")
    success = SucceededJobResult(
        job_id=second.job_id,
        attempt=second.attempt,
        trace_id=second.specification.trace_id,
        started_at=running.started_at or running.leased_at,
        completed_at=datetime.now(UTC),
        outputs=(NamedArtifactReference(name="records", artifact=source),),
    )
    with pytest.raises(InvalidJobState, match="cancelled job cannot publish success"):
        service.publish_result(second.lease_token, success)
    cancelled_result = CancelledJobResult(
        job_id=second.job_id,
        attempt=second.attempt,
        trace_id=second.specification.trace_id,
        started_at=running.started_at or running.leased_at,
        completed_at=datetime.now(UTC),
        reason="live cancellation assertion",
    )
    cancelled = service.publish_result(second.lease_token, cancelled_result)

    assert cancelled.status is JobStatus.CANCELLED
    assert len(service.list_attempts(specification.job_id)) == 2
    assert service.list_logs(first.attempt_id)[0].content == "first attempt failed safely"

    succeeded_specification = legacy_normalization_job(
        source,
        image="hiveblot:test",
        idempotency_key=f"{live_job_prefix}-success",
    )
    service.submit(succeeded_specification)
    lease = service.lease_next(worker_id="live-worker-c", executor_name="fixture", lease_seconds=30)
    assert lease is not None
    attempt = service.mark_running(lease.lease_token)
    result = SucceededJobResult(
        job_id=lease.job_id,
        attempt=lease.attempt,
        trace_id=lease.specification.trace_id,
        started_at=attempt.started_at or attempt.leased_at,
        completed_at=datetime.now(UTC),
        outputs=(NamedArtifactReference(name="records", artifact=source),),
    )
    first_publication = service.publish_result(lease.lease_token, result)
    duplicate_publication = service.publish_result(lease.lease_token, result)
    assert first_publication == duplicate_publication
    assert duplicate_publication.status is JobStatus.SUCCEEDED


@pytest.mark.skipif(
    not DOCKER_LIVE,
    reason="set HIVEBLOT_RUN_LIVE_JOBS=1 and HIVEBLOT_RUN_DOCKER_JOBS=1",
)
def test_legacy_extraction_operation_runs_through_local_docker_job_service(
    tmp_path: Path,
    live_job_prefix: str,
) -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    artifacts, repository, store, source = _live_dependencies(
        settings,
        content=(Path("tests/fixtures/baseline/oduah_2024_page_4_model_output.json").read_bytes()),
    )
    service = JobService(PostgresJobRepository(settings.database_url), artifacts)
    specification = legacy_normalization_job(
        source,
        image=os.getenv("HIVEBLOT_JOB_TEST_IMAGE", "hiveblot:prd-013"),
        idempotency_key=f"{live_job_prefix}-docker",
    )
    service.submit(specification)
    worker = JobWorker(
        service,
        artifacts,
        LocalDockerExecutor(
            VerifiedArtifactReader(artifacts, repository, store),
            runner=SubprocessDockerRunner(docker_binary=settings.job_docker_binary),
            workspace_root=tmp_path,
            max_log_bytes=settings.job_max_log_bytes,
            max_output_bytes=settings.job_max_output_bytes,
        ),
        worker_id="live-docker-worker",
        lease_seconds=settings.job_lease_seconds,
    )

    completed = worker.run_once()

    assert completed is not None
    attempts = service.list_attempts(specification.job_id)
    logs = service.list_logs(attempts[0].attempt_id)
    assert completed.status is JobStatus.SUCCEEDED, (attempts, logs)
    assert completed.result is not None
    output = artifacts.get_artifact(completed.result.outputs[0].artifact.artifact_id)
    download_url, _ = artifacts.create_download_url(output.artifact_id, actor_id=None)
    records = httpx.get(download_url).json()
    assert len(records) == 40
    assert "normalized_records" in service.list_logs(attempts[0].attempt_id)[0].content


def _live_dependencies(
    settings: Settings,
    *,
    content: bytes = b'{"live":true}',
) -> tuple[
    ArtifactService,
    PostgresArtifactRepository,
    S3ObjectStore,
    ArtifactReference,
]:
    store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    store.ensure_bucket()
    repository = PostgresArtifactRepository(settings.database_url)
    service = ArtifactService(
        repository=repository,
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=settings.artifact_max_bytes,
        upload_url_seconds=settings.artifact_upload_url_seconds,
        download_url_seconds=settings.artifact_signed_url_seconds,
    )
    upload = service.begin_multipart_upload(
        original_filename=f"job-input-{uuid4()}.json",
        declared_media_type="application/json",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri=f"urn:hiveblot:test:job-live:{uuid4()}",
        relationships=(),
        actor_id=None,
    )
    response = httpx.put(upload.parts[0].url, content=content)
    response.raise_for_status()
    artifact = service.complete_multipart_upload(
        upload.upload_id,
        (CompletedPart(part_number=1, etag=response.headers["etag"]),),
        actor_id=None,
    ).artifact
    return (
        service,
        repository,
        store,
        ArtifactReference(
            artifact_id=artifact.artifact_id,
            sha256=artifact.sha256,
            media_type=artifact.media_type,
            byte_size=artifact.byte_size,
        ),
    )
