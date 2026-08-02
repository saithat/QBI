"""Opt-in PostgreSQL + S3/MinIO acceptance test for local manual verification."""

import hashlib
import os

import httpx
import pytest
from hiveblot_contracts import ArtifactVisibility
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    PostgresArtifactRepository,
    S3ObjectStore,
    SourceAdapterRegistry,
)

from hiveblot import db
from hiveblot.settings import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_STORAGE") != "1",
    reason="set HIVEBLOT_RUN_LIVE_STORAGE=1 with local PostgreSQL and MinIO",
)


def test_live_multipart_deduplication_and_download_hash() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    store.ensure_bucket()
    service = ArtifactService(
        repository=PostgresArtifactRepository(settings.database_url),
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=settings.artifact_max_bytes,
        upload_url_seconds=settings.artifact_upload_url_seconds,
        download_url_seconds=settings.artifact_signed_url_seconds,
    )
    content = b"%PDF-1.7\nHiveBlot PRD-002 live storage fixture\n%%EOF"

    first = _upload(service, content)
    second = _upload(service, content)
    download_url, _ = service.create_download_url(first.artifact.artifact_id, actor_id=None)
    downloaded = httpx.get(download_url).content

    assert second.artifact.artifact_id == first.artifact.artifact_id
    assert second.deduplicated is True
    assert hashlib.sha256(downloaded).hexdigest() == first.artifact.sha256


def _upload(service: ArtifactService, content: bytes):
    instructions = service.begin_multipart_upload(
        original_filename="prd-002-live-fixture.pdf",
        declared_media_type="application/pdf",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:test:prd-002-live",
        relationships=(),
        actor_id=None,
    )
    upload = httpx.put(instructions.parts[0].url, content=content)
    upload.raise_for_status()
    return service.complete_multipart_upload(
        instructions.upload_id,
        (CompletedPart(part_number=1, etag=upload.headers["etag"]),),
        actor_id=None,
    )
