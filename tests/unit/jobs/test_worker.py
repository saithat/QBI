from __future__ import annotations

from datetime import UTC, datetime

from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactReference,
    ArtifactRelationshipKind,
    ArtifactVisibility,
    JobStatus,
)
from hiveblot_job_service import ExecutionOutcome, ExecutionOutput, JobService, JobWorker
from hiveblot_storage import ArtifactService, CompletedPart, SourceAdapterRegistry

from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore
from tests.fakes.job_service import InMemoryJobRepository
from workers.jobs.specifications import legacy_normalization_job

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


class SuccessfulExecutor:
    name = "fixture-executor"

    def execute(self, lease, *, heartbeat, cancel_requested):
        heartbeat()
        assert not cancel_requested()
        return ExecutionOutcome(
            exit_code=0,
            started_at=NOW,
            completed_at=NOW,
            cancelled=False,
            timed_out=False,
            stdout="normalized 40 records\n",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            outputs=(
                ExecutionOutput(
                    name="records",
                    media_type="application/json",
                    content=b'[{"target":"TP53"}]\n',
                ),
            ),
        )


def test_worker_publishes_queryable_logs_and_provenance_linked_outputs() -> None:
    artifacts, source = _artifacts()
    repository = InMemoryJobRepository()
    service = JobService(repository, artifacts, clock=lambda: NOW)
    specification = legacy_normalization_job(
        source,
        image="hiveblot:test",
        idempotency_key="worker-success",
        submitted_at=NOW,
    )
    service.submit(specification)
    worker = JobWorker(
        service,
        artifacts,
        SuccessfulExecutor(),
        worker_id="worker-a",
        lease_seconds=60,
    )

    completed = worker.run_once()

    assert completed is not None
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.result is not None
    output_reference = completed.result.outputs[0].artifact
    output = artifacts.get_artifact(output_reference.artifact_id)
    assert output.acquisition_method is ArtifactAcquisitionMethod.TOOL_OUTPUT
    assert output.visibility is ArtifactVisibility.PUBLIC
    assert output.relationships[0].kind is ArtifactRelationshipKind.DERIVED_FROM
    assert output.relationships[0].related_artifact_id == source.artifact_id
    attempts = service.list_attempts(specification.job_id)
    assert service.list_logs(attempts[0].attempt_id)[0].content == "normalized 40 records\n"
    assert worker.run_once() is None


def _artifacts() -> tuple[ArtifactService, ArtifactReference]:
    repository = InMemoryArtifactRepository()
    store = InMemoryObjectStore()
    service = ArtifactService(
        repository=repository,
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=1_000_000,
        upload_url_seconds=3600,
        download_url_seconds=900,
        clock=lambda: NOW,
    )
    content = b'{"input":true}'
    upload = service.begin_multipart_upload(
        original_filename="model-output.json",
        declared_media_type="application/json",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:test:worker-input",
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
