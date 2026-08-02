from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import datetime
from typing import BinaryIO
from uuid import UUID, uuid4

from hiveblot_contracts import ArtifactRecord
from hiveblot_storage.errors import ArtifactNotFound, InvalidUploadState
from hiveblot_storage.models import (
    ArtifactEvent,
    ArtifactEventType,
    CompletedPart,
    UploadSession,
    UploadStatus,
)
from hiveblot_storage.object_store import StoredObjectMetadata


class InMemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.object_metadata: dict[str, StoredObjectMetadata] = {}
        self.multipart: dict[str, tuple[str, dict[int, tuple[str, bytes]]]] = {}
        self.abort_count = 0

    def ensure_bucket(self) -> None:
        return None

    def create_multipart(self, key: str) -> str:
        backend_id = str(uuid4())
        self.multipart[backend_id] = (key, {})
        return backend_id

    def put_part(self, backend_upload_id: str, part_number: int, content: bytes) -> str:
        key, parts = self.multipart[backend_upload_id]
        etag = hashlib.md5(content, usedforsecurity=False).hexdigest()
        parts[part_number] = (etag, content)
        self.multipart[backend_upload_id] = (key, parts)
        return etag

    def presign_upload_part(
        self,
        key: str,
        backend_upload_id: str,
        part_number: int,
        expires_in: int,
    ) -> str:
        return f"memory://upload/{backend_upload_id}/{part_number}?key={key}&expires={expires_in}"

    def complete_multipart(
        self,
        key: str,
        backend_upload_id: str,
        parts: Sequence[CompletedPart],
    ) -> None:
        stored_key, uploaded = self.multipart.pop(backend_upload_id)
        assert stored_key == key
        chunks: list[bytes] = []
        for part in sorted(parts, key=lambda item: item.part_number):
            etag, content = uploaded[part.part_number]
            if etag != part.etag:
                raise ValueError("ETag mismatch")
            chunks.append(content)
        content = b"".join(chunks)
        self.objects[key] = content
        self.object_metadata[key] = StoredObjectMetadata(
            byte_size=len(content),
            media_type="application/octet-stream",
            sha256=None,
        )

    def abort_multipart(self, key: str, backend_upload_id: str) -> None:
        stored_key, _ = self.multipart.pop(backend_upload_id)
        assert stored_key == key
        self.abort_count += 1

    def stage_file(self, key: str, stream: BinaryIO) -> None:
        stream.seek(0)
        content = stream.read()
        self.objects[key] = content
        self.object_metadata[key] = StoredObjectMetadata(
            byte_size=len(content),
            media_type="application/octet-stream",
            sha256=None,
        )

    def iter_bytes(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        content = self.objects[key]
        for start in range(0, len(content), chunk_size):
            yield content[start : start + chunk_size]

    def publish(
        self,
        staging_key: str,
        canonical_key: str,
        *,
        media_type: str,
        sha256: str,
        byte_size: int,
    ) -> None:
        content = self.objects[staging_key]
        assert hashlib.sha256(content).hexdigest() == sha256
        assert len(content) == byte_size
        self.objects.setdefault(canonical_key, content)
        self.object_metadata.setdefault(
            canonical_key,
            StoredObjectMetadata(
                byte_size=byte_size,
                media_type=media_type,
                sha256=sha256,
            ),
        )
        self.delete(staging_key)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.object_metadata.pop(key, None)

    def metadata(self, key: str) -> StoredObjectMetadata | None:
        return self.object_metadata.get(key)

    def presign_download(self, key: str, expires_in: int) -> str:
        return f"memory://download/{key}?expires={expires_in}"


class InMemoryArtifactRepository:
    def __init__(self) -> None:
        self.uploads: dict[UUID, UploadSession] = {}
        self.artifacts: dict[UUID, ArtifactRecord] = {}
        self.scope_index: dict[tuple[str, str, UUID | None], UUID] = {}
        self.storage_keys: dict[UUID, str] = {}
        self.events: list[ArtifactEvent] = []

    def _event(
        self,
        *,
        event_type: ArtifactEventType,
        artifact_id: UUID | None = None,
        upload_id: UUID | None = None,
        actor_id: UUID | None = None,
        details: dict[str, object] | None = None,
        created_at: datetime,
    ) -> None:
        self.events.append(
            ArtifactEvent(
                event_id=len(self.events) + 1,
                artifact_id=artifact_id,
                upload_id=upload_id,
                event_type=event_type,
                actor_id=actor_id,
                details=dict(details or {}),
                created_at=created_at,
            )
        )

    def create_upload(self, session: UploadSession, *, actor_id: UUID | None) -> None:
        self.uploads[session.upload_id] = session
        self._event(
            event_type=ArtifactEventType.UPLOAD_INITIATED,
            upload_id=session.upload_id,
            actor_id=actor_id,
            created_at=session.created_at,
        )

    def get_upload(self, upload_id: UUID) -> UploadSession | None:
        return self.uploads.get(upload_id)

    def mark_validating(self, upload_id: UUID, *, updated_at: datetime) -> UploadSession:
        try:
            session = self.uploads[upload_id]
        except KeyError as exc:
            raise ArtifactNotFound(str(upload_id)) from exc
        if session.status is UploadStatus.PUBLISHED:
            return session
        if session.status not in {UploadStatus.INITIATED, UploadStatus.VALIDATING}:
            raise InvalidUploadState(session.status.value)
        session = replace(session, status=UploadStatus.VALIDATING, updated_at=updated_at)
        self.uploads[upload_id] = session
        return session

    def mark_validated(
        self,
        upload_id: UUID,
        *,
        sha256: str,
        media_type: str,
        byte_size: int,
        updated_at: datetime,
    ) -> UploadSession:
        session = self.uploads[upload_id]
        if session.status is not UploadStatus.VALIDATING:
            raise InvalidUploadState(session.status.value)
        session = replace(
            session,
            validated_sha256=sha256,
            validated_media_type=media_type,
            validated_byte_size=byte_size,
            updated_at=updated_at,
        )
        self.uploads[upload_id] = session
        return session

    def mark_failed(self, upload_id: UUID, reason: str, *, updated_at: datetime) -> None:
        session = self.uploads[upload_id]
        if session.status is UploadStatus.PUBLISHED:
            return
        self.uploads[upload_id] = replace(
            session,
            status=UploadStatus.FAILED,
            updated_at=updated_at,
        )
        self._event(
            event_type=ArtifactEventType.UPLOAD_FAILED,
            upload_id=upload_id,
            details={"reason": reason},
            created_at=updated_at,
        )

    def abort_upload(
        self,
        upload_id: UUID,
        *,
        actor_id: UUID | None,
        updated_at: datetime,
    ) -> UploadSession:
        session = self.uploads[upload_id]
        if session.status is UploadStatus.PUBLISHED:
            raise InvalidUploadState("published")
        session = replace(session, status=UploadStatus.ABORTED, updated_at=updated_at)
        self.uploads[upload_id] = session
        self._event(
            event_type=ArtifactEventType.UPLOAD_ABORTED,
            upload_id=upload_id,
            actor_id=actor_id,
            created_at=updated_at,
        )
        return session

    def publish_upload(
        self,
        upload_id: UUID,
        *,
        sha256: str,
        media_type: str,
        byte_size: int,
        storage_key: str,
        actor_id: UUID | None,
        updated_at: datetime,
    ) -> tuple[ArtifactRecord, bool]:
        session = self.uploads[upload_id]
        if session.status is UploadStatus.PUBLISHED:
            assert session.artifact_id is not None
            return self.artifacts[session.artifact_id], True
        scope = (sha256, session.visibility.value, session.organization_id)
        artifact_id = self.scope_index.get(scope)
        deduplicated = artifact_id is not None
        if artifact_id is None:
            artifact_id = uuid4()
            artifact = ArtifactRecord(
                artifact_id=artifact_id,
                sha256=sha256,
                media_type=media_type,
                byte_size=byte_size,
                original_filename=session.original_filename,
                source_uri=session.source_uri,
                acquisition_method=session.acquisition_method,
                visibility=session.visibility,
                organization_id=session.organization_id,
                relationships=session.relationships,
                created_at=updated_at,
            )
            self.artifacts[artifact_id] = artifact
            self.scope_index[scope] = artifact_id
            self.storage_keys[artifact_id] = storage_key
        artifact = self.artifacts[artifact_id]
        self.uploads[upload_id] = replace(
            session,
            status=UploadStatus.PUBLISHED,
            artifact_id=artifact_id,
            updated_at=updated_at,
        )
        self._event(
            event_type=(
                ArtifactEventType.ARTIFACT_DEDUPLICATED
                if deduplicated
                else ArtifactEventType.ARTIFACT_CREATED
            ),
            artifact_id=artifact_id,
            upload_id=upload_id,
            actor_id=actor_id,
            details={
                "sha256": sha256,
                "original_filename": session.original_filename,
                "source_uri": session.source_uri,
                "acquisition_method": session.acquisition_method.value,
            },
            created_at=updated_at,
        )
        return artifact, deduplicated

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord | None:
        return self.artifacts.get(artifact_id)

    def storage_key(self, artifact_id: UUID) -> str | None:
        return self.storage_keys.get(artifact_id)

    def record_access(
        self,
        artifact_id: UUID,
        *,
        actor_id: UUID | None,
        expires_in: int,
    ) -> None:
        created_at = self.artifacts[artifact_id].created_at
        self._event(
            event_type=ArtifactEventType.ACCESS_URL_CREATED,
            artifact_id=artifact_id,
            actor_id=actor_id,
            details={"expires_in_seconds": expires_in},
            created_at=created_at,
        )

    def list_events(self, artifact_id: UUID) -> Sequence[ArtifactEvent]:
        return tuple(event for event in self.events if event.artifact_id == artifact_id)
