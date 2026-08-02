"""Publication orchestration for immutable scientific artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactRelationship,
    ArtifactRelationshipKind,
    ArtifactVisibility,
)

from .errors import ArtifactNotFound, InvalidArtifact, InvalidUploadState
from .media import SNIFF_BYTES, detect_media_type, validate_declared_media_type
from .models import ArtifactEvent, CompletedPart, UploadSession, UploadStatus
from .object_store import ObjectStore
from .repository import ArtifactRepository
from .source_adapters import SourceAdapterRegistry


@dataclass(frozen=True, slots=True)
class UploadPartInstruction:
    part_number: int
    url: str


@dataclass(frozen=True, slots=True)
class UploadInstructions:
    upload_id: UUID
    expires_at: datetime
    parts: tuple[UploadPartInstruction, ...]


@dataclass(frozen=True, slots=True)
class PublishedArtifact:
    artifact: ArtifactRecord
    deduplicated: bool


class ArtifactService:
    """Coordinates staging, validation, immutable publication, and provenance."""

    def __init__(
        self,
        *,
        repository: ArtifactRepository,
        object_store: ObjectStore,
        source_adapters: SourceAdapterRegistry,
        max_bytes: int,
        upload_url_seconds: int,
        download_url_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._object_store = object_store
        self._source_adapters = source_adapters
        self._max_bytes = max_bytes
        self._upload_url_seconds = upload_url_seconds
        self._download_url_seconds = download_url_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def begin_multipart_upload(
        self,
        *,
        original_filename: str,
        declared_media_type: str | None,
        expected_byte_size: int | None,
        part_count: int,
        visibility: ArtifactVisibility,
        organization_id: UUID | None,
        source_uri: str | None,
        relationships: tuple[ArtifactRelationship, ...],
        actor_id: UUID | None,
    ) -> UploadInstructions:
        _validate_metadata(
            original_filename=original_filename,
            expected_byte_size=expected_byte_size,
            max_bytes=self._max_bytes,
            visibility=visibility,
            organization_id=organization_id,
        )
        self._validate_relationships(relationships)
        if not 1 <= part_count <= 10_000:
            raise InvalidArtifact("part_count must be between 1 and 10000")

        now = self._clock()
        upload_id = uuid4()
        staging_key = f"staging/uploads/{upload_id}"
        backend_upload_id = self._object_store.create_multipart(staging_key)
        session = UploadSession(
            upload_id=upload_id,
            backend_upload_id=backend_upload_id,
            staging_key=staging_key,
            status=UploadStatus.INITIATED,
            original_filename=original_filename.strip(),
            declared_media_type=declared_media_type,
            expected_byte_size=expected_byte_size,
            source_uri=source_uri,
            acquisition_method=ArtifactAcquisitionMethod.USER_UPLOAD,
            visibility=visibility,
            organization_id=organization_id,
            relationships=relationships,
            validated_sha256=None,
            validated_media_type=None,
            validated_byte_size=None,
            artifact_id=None,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=self._upload_url_seconds),
        )
        try:
            self._repository.create_upload(session, actor_id=actor_id)
        except Exception:
            self._object_store.abort_multipart(staging_key, backend_upload_id)
            raise

        return UploadInstructions(
            upload_id=upload_id,
            expires_at=session.expires_at,
            parts=tuple(
                UploadPartInstruction(
                    part_number=part_number,
                    url=self._object_store.presign_upload_part(
                        staging_key,
                        backend_upload_id,
                        part_number,
                        self._upload_url_seconds,
                    ),
                )
                for part_number in range(1, part_count + 1)
            ),
        )

    def complete_multipart_upload(
        self,
        upload_id: UUID,
        parts: Sequence[CompletedPart],
        *,
        actor_id: UUID | None,
    ) -> PublishedArtifact:
        if not parts:
            raise InvalidArtifact("at least one completed part is required")
        part_numbers = [part.part_number for part in parts]
        if (
            len(set(part_numbers)) != len(part_numbers)
            or min(part_numbers) < 1
            or max(part_numbers) > 10_000
        ):
            raise InvalidArtifact("completed part numbers must be unique and between 1 and 10000")

        session = self._repository.mark_validating(upload_id, updated_at=self._clock())
        if session.status is UploadStatus.PUBLISHED:
            if session.artifact_id is None:
                raise InvalidUploadState("published upload is missing its artifact reference")
            artifact = self.get_artifact(session.artifact_id)
            return PublishedArtifact(artifact=artifact, deduplicated=True)
        if session.backend_upload_id is None:
            raise InvalidUploadState("upload is not a multipart upload")

        try:
            if (
                session.validated_sha256 is None
                and self._object_store.metadata(session.staging_key) is None
            ):
                self._object_store.complete_multipart(
                    session.staging_key,
                    session.backend_upload_id,
                    parts,
                )
            return self._validate_and_publish(session, actor_id=actor_id)
        except (InvalidArtifact, InvalidUploadState) as exc:
            self._object_store.delete(session.staging_key)
            self._repository.mark_failed(upload_id, str(exc), updated_at=self._clock())
            raise

    def ingest_from_source(
        self,
        *,
        adapter_name: str,
        source_uri: str,
        visibility: ArtifactVisibility,
        organization_id: UUID | None,
        relationships: tuple[ArtifactRelationship, ...],
        actor_id: UUID | None,
    ) -> PublishedArtifact:
        adapter = self._source_adapters.get(adapter_name)
        self._validate_relationships(relationships)
        with adapter.fetch(source_uri) as payload:
            _validate_metadata(
                original_filename=payload.original_filename,
                expected_byte_size=None,
                max_bytes=self._max_bytes,
                visibility=visibility,
                organization_id=organization_id,
            )
            now = self._clock()
            upload_id = uuid4()
            session = UploadSession(
                upload_id=upload_id,
                backend_upload_id=None,
                staging_key=f"staging/sources/{upload_id}",
                status=UploadStatus.INITIATED,
                original_filename=payload.original_filename,
                declared_media_type=payload.declared_media_type,
                expected_byte_size=None,
                source_uri=source_uri,
                acquisition_method=ArtifactAcquisitionMethod.SOURCE_ADAPTER,
                visibility=visibility,
                organization_id=organization_id,
                relationships=relationships,
                validated_sha256=None,
                validated_media_type=None,
                validated_byte_size=None,
                artifact_id=None,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=self._upload_url_seconds),
            )
            self._repository.create_upload(session, actor_id=actor_id)
            try:
                self._object_store.stage_file(session.staging_key, payload.stream)
                session = self._repository.mark_validating(upload_id, updated_at=self._clock())
                return self._validate_and_publish(session, actor_id=actor_id)
            except Exception as exc:
                self._object_store.delete(session.staging_key)
                self._repository.mark_failed(upload_id, str(exc), updated_at=self._clock())
                raise

    def publish_tool_output(
        self,
        *,
        original_filename: str,
        declared_media_type: str,
        content: bytes,
        source_uri: str,
        visibility: ArtifactVisibility,
        organization_id: UUID | None,
        relationships: tuple[ArtifactRelationship, ...],
        actor_id: UUID | None,
    ) -> PublishedArtifact:
        """Publish deterministic generated bytes through the immutable artifact boundary."""

        _validate_metadata(
            original_filename=original_filename,
            expected_byte_size=len(content),
            max_bytes=self._max_bytes,
            visibility=visibility,
            organization_id=organization_id,
        )
        self._validate_relationships(relationships)
        if not any(
            relationship.kind is ArtifactRelationshipKind.DERIVED_FROM
            for relationship in relationships
        ):
            raise InvalidArtifact("tool output requires a derived-from artifact relationship")
        now = self._clock()
        upload_id = uuid4()
        session = UploadSession(
            upload_id=upload_id,
            backend_upload_id=None,
            staging_key=f"staging/tool-outputs/{upload_id}",
            status=UploadStatus.INITIATED,
            original_filename=original_filename.strip(),
            declared_media_type=declared_media_type,
            expected_byte_size=len(content),
            source_uri=source_uri,
            acquisition_method=ArtifactAcquisitionMethod.TOOL_OUTPUT,
            visibility=visibility,
            organization_id=organization_id,
            relationships=relationships,
            validated_sha256=None,
            validated_media_type=None,
            validated_byte_size=None,
            artifact_id=None,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=self._upload_url_seconds),
        )
        self._repository.create_upload(session, actor_id=actor_id)
        try:
            self._object_store.stage_file(session.staging_key, BytesIO(content))
            session = self._repository.mark_validating(upload_id, updated_at=self._clock())
            return self._validate_and_publish(session, actor_id=actor_id)
        except Exception as exc:
            self._object_store.delete(session.staging_key)
            self._repository.mark_failed(upload_id, str(exc), updated_at=self._clock())
            raise

    def abort_upload(self, upload_id: UUID, *, actor_id: UUID | None) -> None:
        session = self._repository.get_upload(upload_id)
        if session is None:
            raise ArtifactNotFound(f"upload {upload_id} does not exist")
        if session.backend_upload_id is not None and session.status is UploadStatus.INITIATED:
            self._object_store.abort_multipart(
                session.staging_key,
                session.backend_upload_id,
            )
        if self._object_store.metadata(session.staging_key) is not None:
            self._object_store.delete(session.staging_key)
        self._repository.abort_upload(upload_id, actor_id=actor_id, updated_at=self._clock())

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord:
        artifact = self._repository.get_artifact(artifact_id)
        if artifact is None:
            raise ArtifactNotFound(f"artifact {artifact_id} does not exist")
        return artifact

    def create_download_url(
        self,
        artifact_id: UUID,
        *,
        actor_id: UUID | None,
    ) -> tuple[str, datetime]:
        artifact = self.get_artifact(artifact_id)
        key = self._repository.storage_key(artifact_id)
        if key is None:
            raise ArtifactNotFound(f"artifact {artifact_id} has no stored object")
        metadata = self._object_store.metadata(key)
        if (
            metadata is None
            or metadata.byte_size != artifact.byte_size
            or metadata.sha256 != artifact.sha256
        ):
            raise InvalidArtifact("stored object metadata does not match the artifact record")
        self._repository.record_access(
            artifact_id,
            actor_id=actor_id,
            expires_in=self._download_url_seconds,
        )
        return (
            self._object_store.presign_download(key, self._download_url_seconds),
            self._clock() + timedelta(seconds=self._download_url_seconds),
        )

    def list_events(self, artifact_id: UUID) -> Sequence[ArtifactEvent]:
        self.get_artifact(artifact_id)
        return self._repository.list_events(artifact_id)

    def _validate_and_publish(
        self,
        session: UploadSession,
        *,
        actor_id: UUID | None,
    ) -> PublishedArtifact:
        if session.validated_sha256 is None:
            sha256, media_type, byte_size = self._inspect_staged(session)
            session = self._repository.mark_validated(
                session.upload_id,
                sha256=sha256,
                media_type=media_type,
                byte_size=byte_size,
                updated_at=self._clock(),
            )
        else:
            sha256 = session.validated_sha256
            validated_media_type = session.validated_media_type
            validated_byte_size = session.validated_byte_size
            if validated_media_type is None or validated_byte_size is None:
                raise InvalidUploadState("validated upload metadata is incomplete")
            media_type = validated_media_type
            byte_size = validated_byte_size

        canonical_key = _canonical_key(sha256)
        canonical_metadata = self._object_store.metadata(canonical_key)
        if canonical_metadata is None:
            if self._object_store.metadata(session.staging_key) is None:
                raise InvalidUploadState("validated upload lost both staged and canonical bytes")
            self._object_store.publish(
                session.staging_key,
                canonical_key,
                media_type=media_type,
                sha256=sha256,
                byte_size=byte_size,
            )
        elif (
            canonical_metadata.byte_size != byte_size
            or canonical_metadata.sha256 != sha256
            or canonical_metadata.media_type != media_type
        ):
            raise InvalidArtifact("canonical object metadata conflicts with validated bytes")
        elif self._object_store.metadata(session.staging_key) is not None:
            self._object_store.delete(session.staging_key)

        artifact, deduplicated = self._repository.publish_upload(
            session.upload_id,
            sha256=sha256,
            media_type=media_type,
            byte_size=byte_size,
            storage_key=canonical_key,
            actor_id=actor_id,
            updated_at=self._clock(),
        )
        return PublishedArtifact(artifact=artifact, deduplicated=deduplicated)

    def _inspect_staged(self, session: UploadSession) -> tuple[str, str, int]:
        digest = hashlib.sha256()
        sample = bytearray()
        byte_size = 0
        for chunk in self._object_store.iter_bytes(session.staging_key):
            byte_size += len(chunk)
            if byte_size > self._max_bytes:
                raise InvalidArtifact("artifact exceeds the configured size limit")
            digest.update(chunk)
            if len(sample) < SNIFF_BYTES:
                sample.extend(chunk[: SNIFF_BYTES - len(sample)])
        if session.expected_byte_size is not None and byte_size != session.expected_byte_size:
            raise InvalidArtifact(
                f"uploaded size {byte_size} does not match expected size "
                f"{session.expected_byte_size}"
            )
        media_type = detect_media_type(
            bytes(sample),
            sample_is_complete=byte_size <= SNIFF_BYTES,
        )
        validate_declared_media_type(media_type, session.declared_media_type)
        return digest.hexdigest(), media_type, byte_size

    def _validate_relationships(
        self,
        relationships: tuple[ArtifactRelationship, ...],
    ) -> None:
        for relationship in relationships:
            if self._repository.get_artifact(relationship.related_artifact_id) is None:
                raise InvalidArtifact(
                    f"related artifact {relationship.related_artifact_id} does not exist"
                )


def _validate_metadata(
    *,
    original_filename: str,
    expected_byte_size: int | None,
    max_bytes: int,
    visibility: ArtifactVisibility,
    organization_id: UUID | None,
) -> None:
    filename = original_filename.strip()
    if not filename or len(filename) > 1024 or "\x00" in filename:
        raise InvalidArtifact("original_filename must contain 1 to 1024 safe characters")
    if expected_byte_size is not None and not 0 <= expected_byte_size <= max_bytes:
        raise InvalidArtifact("expected byte size exceeds the configured limit")
    if visibility is ArtifactVisibility.PUBLIC and organization_id is not None:
        raise InvalidArtifact("public artifacts cannot include an organization_id")
    if visibility is ArtifactVisibility.ORGANIZATION_PRIVATE and organization_id is None:
        raise InvalidArtifact("organization-private artifacts require an organization_id")


def _canonical_key(sha256: str) -> str:
    return f"artifacts/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"
