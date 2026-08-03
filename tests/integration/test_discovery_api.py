from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactReference,
    ArtifactVisibility,
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
from hiveblot_crawler import FrontierService

from apps.api.application import app
from apps.api.discovery_dependencies import get_frontier_service
from tests.fakes.discovery import InMemoryFrontierRepository

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
TRACE_ID = UUID("00000000-0000-0000-0000-000000000123")


class _Artifacts:
    def __init__(self, artifact: ArtifactRecord) -> None:
        self._artifact = artifact

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord:
        assert artifact_id == self._artifact.artifact_id
        return self._artifact


def _batch() -> DiscoveryBatch:
    record = DiscoveryRecord(
        identity_key="pmc:pmc123",
        source_record_id="oai:pubmedcentral.nih.gov:123",
        accession="PMC123",
        entity_kind=DiscoveryEntityKind.PAPER,
        canonical_url="https://example.test/oai?record=123#ignored",
        acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
        discovered_at=NOW,
        next_eligible_fetch_at=NOW,
        robots_status=DiscoveryRobotsStatus.NOT_APPLICABLE,
        expected_media_types=("application/xml",),
        license=DiscoveryLicense(
            status=DiscoveryLicenseStatus.UNVERIFIED,
            statement="verify article-level license",
        ),
        access_status=DiscoveryAccessStatus.ALLOWED,
        trace_id=TRACE_ID,
    )
    return DiscoveryBatch(
        batch_id=uuid4(),
        source=ToolIdentifier(name="pmc-oai", version="1.0.0"),
        query=DiscoveryQuery(maximum_pages=1),
        trace_id=TRACE_ID,
        started_at=NOW,
        completed_at=NOW,
        records=(record,),
        evidence=(
            DiscoveryEvidence(
                request_url="https://example.test/oai",
                response_artifact=ArtifactReference(
                    artifact_id=uuid4(),
                    sha256="a" * 64,
                    media_type="application/xml",
                    byte_size=1,
                ),
                response_sha256="a" * 64,
                response_media_type="application/xml",
                http_status=200,
                fetched_at=NOW,
            ),
        ),
    )


def test_discovery_api_ingests_inspects_schedules_and_links_acquisitions() -> None:
    artifact = ArtifactRecord(
        artifact_id=uuid4(),
        sha256="b" * 64,
        media_type="application/xml",
        byte_size=123,
        original_filename="PMC123.xml",
        source_uri="https://example.test/oai?record=123",
        acquisition_method=ArtifactAcquisitionMethod.SOURCE_ADAPTER,
        visibility=ArtifactVisibility.PUBLIC,
        created_at=NOW,
    )
    service = FrontierService(
        InMemoryFrontierRepository(),
        _Artifacts(artifact),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    app.dependency_overrides[get_frontier_service] = lambda: service
    client = TestClient(app)
    batch = _batch()
    try:
        invalid = client.post(
            "/api/v1/discovery-batches",
            json={
                "schema_version": "1.0",
                "batch": batch.model_dump(mode="json"),
                "unknown": True,
            },
        )
        assert invalid.status_code == 422

        ingested = client.post(
            "/api/v1/discovery-batches",
            json={
                "schema_version": "1.0",
                "batch": batch.model_dump(mode="json"),
            },
        )
        assert ingested.status_code == 201, ingested.text
        record = ingested.json()["records"][0]
        frontier_id = record["frontier_id"]
        assert record["canonical_url"] == "https://example.test/oai?record=123"

        replayed = client.post(
            "/api/v1/discovery-batches",
            json={
                "schema_version": "1.0",
                "batch": batch.model_dump(mode="json"),
            },
        )
        assert replayed.status_code == 201
        assert replayed.json() == ingested.json()

        changed_batch = batch.model_dump(mode="json")
        changed_batch["records"][0]["priority"] = 999
        conflicting = client.post(
            "/api/v1/discovery-batches",
            json={"schema_version": "1.0", "batch": changed_batch},
        )
        assert conflicting.status_code == 409

        listed = client.get("/api/v1/discovery-frontier?status=pending&missing_artifact=true")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        scheduled = client.patch(
            f"/api/v1/discovery-frontier/{frontier_id}/schedule",
            json={
                "schema_version": "1.0",
                "expected_version": 1,
                "priority": 800,
                "next_eligible_fetch_at": "2026-08-03T12:00:00Z",
            },
        )
        assert scheduled.status_code == 200, scheduled.text
        assert scheduled.json()["priority"] == 800

        stale = client.patch(
            f"/api/v1/discovery-frontier/{frontier_id}/schedule",
            json={
                "schema_version": "1.0",
                "expected_version": 1,
                "priority": 1,
                "next_eligible_fetch_at": "2026-08-03T12:00:00Z",
            },
        )
        assert stale.status_code == 409

        retried = client.post(
            f"/api/v1/discovery-frontier/{frontier_id}/retry",
            json={
                "schema_version": "1.0",
                "expected_version": 2,
                "next_eligible_fetch_at": "2026-08-03T13:00:00Z",
                "rationale": "operator approved another attempt",
            },
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["version"] == 3

        acquired = client.post(
            f"/api/v1/discovery-frontier/{frontier_id}/acquisitions",
            json={
                "schema_version": "1.0",
                "expected_version": 3,
                "artifact_id": str(artifact.artifact_id),
                "trace_id": str(TRACE_ID),
            },
        )
        assert acquired.status_code == 200, acquired.text
        assert acquired.json()["status"] == "acquired"
        assert acquired.json()["acquisitions"][0]["artifact"]["sha256"] == "b" * 64

        detail = client.get(f"/api/v1/discovery-frontier/{frontier_id}")
        assert detail.status_code == 200
        assert detail.json()["version"] == 4
    finally:
        app.dependency_overrides.pop(get_frontier_service, None)
