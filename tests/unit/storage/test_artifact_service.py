from __future__ import annotations

import io
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRelationship,
    ArtifactRelationshipKind,
    ArtifactVisibility,
)
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    InvalidArtifact,
    SourceAdapterRegistry,
)
from hiveblot_storage.models import ArtifactEventType, UploadStatus
from hiveblot_storage.source_adapters import SourcePayload

from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
PDF_BYTES = b"%PDF-1.7\nrepresentative western blot source\n%%EOF"


class StaticSourceAdapter:
    @contextmanager
    def fetch(self, _source_uri: str) -> AbstractContextManager[SourcePayload]:
        with io.BytesIO(PDF_BYTES) as stream:
            yield SourcePayload(
                stream=stream,
                original_filename="paper.bin",
                declared_media_type="application/pdf",
            )


def make_service(
    *,
    adapters: dict[str, object] | None = None,
) -> tuple[ArtifactService, InMemoryArtifactRepository, InMemoryObjectStore]:
    repository = InMemoryArtifactRepository()
    store = InMemoryObjectStore()
    service = ArtifactService(
        repository=repository,
        object_store=store,
        source_adapters=SourceAdapterRegistry(adapters or {}),  # type: ignore[arg-type]
        max_bytes=10_000_000,
        upload_url_seconds=3600,
        download_url_seconds=900,
        clock=lambda: NOW,
    )
    return service, repository, store


def upload_bytes(
    service: ArtifactService,
    repository: InMemoryArtifactRepository,
    store: InMemoryObjectStore,
    content: bytes,
    *,
    filename: str = "paper.pdf",
    declared_media_type: str = "application/pdf",
    relationships: tuple[ArtifactRelationship, ...] = (),
):
    instructions = service.begin_multipart_upload(
        original_filename=filename,
        declared_media_type=declared_media_type,
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri=None,
        relationships=relationships,
        actor_id=None,
    )
    session = repository.uploads[instructions.upload_id]
    assert session.backend_upload_id is not None
    etag = store.put_part(session.backend_upload_id, 1, content)
    return service.complete_multipart_upload(
        instructions.upload_id,
        (CompletedPart(part_number=1, etag=etag),),
        actor_id=None,
    )


def test_identical_uploads_return_one_canonical_artifact() -> None:
    service, repository, store = make_service()

    first = upload_bytes(service, repository, store, PDF_BYTES)
    second = upload_bytes(service, repository, store, PDF_BYTES, filename="duplicate.pdf")

    assert first.deduplicated is False
    assert second.deduplicated is True
    assert second.artifact.artifact_id == first.artifact.artifact_id
    assert len(repository.artifacts) == 1
    canonical_keys = [key for key in store.objects if key.startswith("artifacts/sha256/")]
    assert len(canonical_keys) == 1
    assert b"".join(store.iter_bytes(canonical_keys[0])) == PDF_BYTES


def test_interrupted_upload_never_creates_a_published_artifact() -> None:
    service, repository, store = make_service()
    instructions = service.begin_multipart_upload(
        original_filename="interrupted.pdf",
        declared_media_type="application/pdf",
        expected_byte_size=len(PDF_BYTES),
        part_count=2,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri=None,
        relationships=(),
        actor_id=None,
    )

    assert repository.artifacts == {}
    service.abort_upload(instructions.upload_id, actor_id=None)

    assert repository.uploads[instructions.upload_id].status is UploadStatus.ABORTED
    assert repository.artifacts == {}
    assert store.abort_count == 1


def test_published_artifact_preserves_related_artifact_links() -> None:
    service, repository, store = make_service()
    parent = upload_bytes(service, repository, store, PDF_BYTES)
    relationship = ArtifactRelationship(
        related_artifact_id=parent.artifact.artifact_id,
        kind=ArtifactRelationshipKind.PARENT,
    )

    child = upload_bytes(
        service,
        repository,
        store,
        b"%PDF-1.7\nchild artifact\n%%EOF",
        relationships=(relationship,),
    )

    assert child.artifact.relationships == (relationship,)


def test_upload_rejects_relationship_to_missing_artifact() -> None:
    service, _, _ = make_service()
    relationship = ArtifactRelationship(
        related_artifact_id=uuid4(),
        kind=ArtifactRelationshipKind.RELATED,
    )

    with pytest.raises(InvalidArtifact, match="does not exist"):
        service.begin_multipart_upload(
            original_filename="child.pdf",
            declared_media_type="application/pdf",
            expected_byte_size=len(PDF_BYTES),
            part_count=1,
            visibility=ArtifactVisibility.PUBLIC,
            organization_id=None,
            source_uri=None,
            relationships=(relationship,),
            actor_id=None,
        )


def test_media_type_is_checked_independently_from_filename() -> None:
    service, repository, store = make_service()
    png = b"\x89PNG\r\n\x1a\nnot-a-real-image-but-valid-signature"

    result = upload_bytes(
        service,
        repository,
        store,
        png,
        filename="misleading.pdf",
        declared_media_type="image/png",
    )

    assert result.artifact.media_type == "image/png"
    assert result.artifact.original_filename == "misleading.pdf"


def test_mismatched_declared_type_fails_without_artifact() -> None:
    service, repository, store = make_service()
    instructions = service.begin_multipart_upload(
        original_filename="wrong.pdf",
        declared_media_type="application/pdf",
        expected_byte_size=12,
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri=None,
        relationships=(),
        actor_id=None,
    )
    session = repository.uploads[instructions.upload_id]
    assert session.backend_upload_id is not None
    content = b"not a format"
    etag = store.put_part(session.backend_upload_id, 1, content)

    with pytest.raises(InvalidArtifact):
        service.complete_multipart_upload(
            instructions.upload_id,
            (CompletedPart(part_number=1, etag=etag),),
            actor_id=None,
        )

    assert repository.artifacts == {}
    assert repository.uploads[instructions.upload_id].status is UploadStatus.FAILED


def test_signed_download_url_expires_and_records_access() -> None:
    service, repository, store = make_service()
    result = upload_bytes(service, repository, store, PDF_BYTES)

    url, expires_at = service.create_download_url(result.artifact.artifact_id, actor_id=None)

    assert url.endswith("?expires=900")
    assert expires_at == NOW + timedelta(seconds=900)
    assert repository.events[-1].event_type is ArtifactEventType.ACCESS_URL_CREATED


def test_server_side_adapter_preserves_source_provenance() -> None:
    service, repository, _ = make_service(adapters={"fixture": StaticSourceAdapter()})
    source_uri = "https://repository.example/papers/one"

    result = service.ingest_from_source(
        adapter_name="fixture",
        source_uri=source_uri,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        relationships=(),
        actor_id=uuid4(),
    )

    assert result.artifact.source_uri == source_uri
    assert result.artifact.acquisition_method is ArtifactAcquisitionMethod.SOURCE_ADAPTER
    assert result.artifact.media_type == "application/pdf"
    assert len(repository.artifacts) == 1


def test_already_fetched_source_payload_is_published_exactly_and_deduplicated() -> None:
    service, repository, store = make_service()
    content = b'<?xml version="1.0"?><OAI-PMH><record /></OAI-PMH>'

    first = service.publish_source_payload(
        original_filename="response.xml",
        declared_media_type="text/xml",
        content=content,
        source_uri="https://repository.example/oai?verb=ListIdentifiers",
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        relationships=(),
        actor_id=None,
    )
    second = service.publish_source_payload(
        original_filename="response-again.xml",
        declared_media_type="application/xml",
        content=content,
        source_uri="https://repository.example/oai?verb=ListIdentifiers",
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        relationships=(),
        actor_id=None,
    )

    assert first.artifact.acquisition_method is ArtifactAcquisitionMethod.SOURCE_ADAPTER
    assert first.artifact.media_type == "application/xml"
    assert second.deduplicated is True
    assert second.artifact.artifact_id == first.artifact.artifact_id
    canonical_key = repository.storage_keys[first.artifact.artifact_id]
    assert b"".join(store.iter_bytes(canonical_key)) == content


def test_tool_output_is_immutable_deduplicated_and_derived() -> None:
    service, repository, store = make_service()
    source = upload_bytes(service, repository, store, PDF_BYTES)
    relationship = ArtifactRelationship(
        related_artifact_id=source.artifact.artifact_id,
        kind=ArtifactRelationshipKind.DERIVED_FROM,
    )
    png = b"\x89PNG\r\n\x1a\ndeterministic-overlay"

    first = service.publish_tool_output(
        original_filename="overlay.png",
        declared_media_type="image/png",
        content=png,
        source_uri="urn:hiveblot:tool:test:one",
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        relationships=(relationship,),
        actor_id=None,
    )
    second = service.publish_tool_output(
        original_filename="overlay-again.png",
        declared_media_type="image/png",
        content=png,
        source_uri="urn:hiveblot:tool:test:one",
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        relationships=(relationship,),
        actor_id=None,
    )

    assert first.artifact.acquisition_method is ArtifactAcquisitionMethod.TOOL_OUTPUT
    assert first.artifact.relationships == (relationship,)
    assert second.deduplicated is True
    assert second.artifact.artifact_id == first.artifact.artifact_id


def test_tool_output_requires_derived_from_provenance() -> None:
    service, _, _ = make_service()

    with pytest.raises(InvalidArtifact, match="derived-from"):
        service.publish_tool_output(
            original_filename="overlay.png",
            declared_media_type="image/png",
            content=b"\x89PNG\r\n\x1a\nno-parent",
            source_uri="urn:hiveblot:tool:test:missing-parent",
            visibility=ArtifactVisibility.PUBLIC,
            organization_id=None,
            relationships=(),
            actor_id=None,
        )


def test_private_upload_requires_organization_scope() -> None:
    service, _, _ = make_service()
    with pytest.raises(InvalidArtifact, match="organization_id"):
        service.begin_multipart_upload(
            original_filename="private.pdf",
            declared_media_type="application/pdf",
            expected_byte_size=len(PDF_BYTES),
            part_count=1,
            visibility=ArtifactVisibility.ORGANIZATION_PRIVATE,
            organization_id=None,
            source_uri=None,
            relationships=(),
            actor_id=UUID("00000000-0000-0000-0000-000000000001"),
        )
