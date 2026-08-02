from datetime import UTC, datetime

from fastapi.testclient import TestClient
from hiveblot_contracts import ArtifactReference, ArtifactVisibility
from hiveblot_job_service import JobService
from hiveblot_storage import ArtifactService, CompletedPart, SourceAdapterRegistry

from apps.api.application import app
from apps.api.job_dependencies import get_job_service
from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore
from tests.fakes.job_service import InMemoryJobRepository
from workers.jobs.specifications import legacy_normalization_job

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def test_job_api_submits_lists_inspects_and_cancels_strict_jobs() -> None:
    artifacts, reference = _artifacts()
    service = JobService(InMemoryJobRepository(), artifacts, clock=lambda: NOW)
    specification = legacy_normalization_job(
        reference,
        image="hiveblot:test",
        idempotency_key="api-job",
        submitted_at=NOW,
    )
    app.dependency_overrides[get_job_service] = lambda: service
    client = TestClient(app)
    try:
        invalid = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "specification": specification.model_dump(mode="json"),
                "unknown": True,
            },
        )
        assert invalid.status_code == 422

        submitted = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "specification": specification.model_dump(mode="json"),
            },
        )
        assert submitted.status_code == 201, submitted.text
        assert submitted.json()["status"] == "pending"

        listed = client.get("/api/v1/jobs?status=pending")
        detail = client.get(f"/api/v1/jobs/{specification.job_id}")
        attempts = client.get(f"/api/v1/jobs/{specification.job_id}/attempts")
        cancelled = client.post(
            f"/api/v1/jobs/{specification.job_id}/cancel",
            json={"schema_version": "1.0", "reason": "no longer needed"},
        )

        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert detail.status_code == 200
        assert attempts.json() == []
        assert cancelled.json()["status"] == "cancelled"
    finally:
        app.dependency_overrides.clear()


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
        original_filename="input.json",
        declared_media_type="application/json",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:test:job-api",
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
