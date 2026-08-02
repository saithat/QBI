from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from hiveblot_storage import ArtifactService, SourceAdapterRegistry

from apps.api.artifact_dependencies import get_artifact_service
from apps.api.main import app
from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore


@pytest.mark.asyncio
async def test_multipart_artifact_api_round_trip_and_strict_validation() -> None:
    content = b"%PDF-1.7\n%%EOF"
    repository = InMemoryArtifactRepository()
    store = InMemoryObjectStore()
    service = ArtifactService(
        repository=repository,
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=1_000_000,
        upload_url_seconds=3600,
        download_url_seconds=900,
        clock=lambda: datetime(2026, 8, 2, 12, tzinfo=UTC),
    )
    app.dependency_overrides[get_artifact_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            invalid = await client.post(
                "/api/v1/artifact-uploads",
                json={
                    "schema_version": "1.0",
                    "original_filename": "paper.pdf",
                    "declared_media_type": "application/pdf",
                    "expected_byte_size": len(content),
                    "part_count": 1,
                    "visibility": "public",
                    "relationships": [],
                    "unknown": "rejected",
                },
            )
            assert invalid.status_code == 422

            private = await client.post(
                "/api/v1/artifact-uploads",
                json={
                    "schema_version": "1.0",
                    "original_filename": "private.pdf",
                    "declared_media_type": "application/pdf",
                    "expected_byte_size": len(content),
                    "part_count": 1,
                    "visibility": "organization_private",
                    "organization_id": "00000000-0000-0000-0000-000000000123",
                    "relationships": [],
                },
            )
            assert private.status_code == 201
            await client.delete(f"/api/v1/artifact-uploads/{private.json()['upload_id']}")

            begin = await client.post(
                "/api/v1/artifact-uploads",
                json={
                    "schema_version": "1.0",
                    "original_filename": "paper.pdf",
                    "declared_media_type": "application/pdf",
                    "expected_byte_size": len(content),
                    "part_count": 1,
                    "visibility": "public",
                    "relationships": [],
                },
            )
            assert begin.status_code == 201
            upload_id = UUID(begin.json()["upload_id"])
            session = repository.uploads[upload_id]
            assert session.backend_upload_id is not None
            etag = store.put_part(session.backend_upload_id, 1, content)

            complete = await client.post(
                f"/api/v1/artifact-uploads/{upload_id}/complete",
                json={
                    "schema_version": "1.0",
                    "parts": [
                        {
                            "schema_version": "1.0",
                            "part_number": 1,
                            "etag": etag,
                        }
                    ],
                },
            )
            assert complete.status_code == 200
            artifact = complete.json()["artifact"]
            assert artifact["media_type"] == "application/pdf"
            artifact_id = artifact["artifact_id"]

            metadata = await client.get(f"/api/v1/artifacts/{artifact_id}")
            signed = await client.post(f"/api/v1/artifacts/{artifact_id}/download-url")
            events = await client.get(f"/api/v1/artifacts/{artifact_id}/events")

            assert metadata.status_code == 200
            assert signed.status_code == 200
            assert signed.json()["expires_at"] == "2026-08-02T12:15:00Z"
            assert [item["event_type"] for item in events.json()["events"]] == [
                "artifact_created",
                "access_url_created",
            ]
    finally:
        app.dependency_overrides.pop(get_artifact_service, None)
