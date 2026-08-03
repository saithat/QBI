from __future__ import annotations

from datetime import UTC, datetime

from hiveblot_crawler import ArtifactDiscoveryEvidencePublisher
from hiveblot_storage import ArtifactService, SourceAdapterRegistry

from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore


def test_discovery_evidence_publisher_preserves_exact_raw_response_bytes() -> None:
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
    content = b'<?xml version="1.0"?><OAI-PMH><record /></OAI-PMH>'
    reference = ArtifactDiscoveryEvidencePublisher(service).publish_response(
        request_url="https://example.test/oai?verb=ListIdentifiers",
        media_type="application/xml",
        content=content,
    )

    record = repository.artifacts[reference.artifact_id]
    assert record.sha256 == reference.sha256
    assert record.source_uri == "https://example.test/oai?verb=ListIdentifiers"
    assert b"".join(store.iter_bytes(repository.storage_keys[record.artifact_id])) == content
