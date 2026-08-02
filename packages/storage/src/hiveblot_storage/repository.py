"""Artifact persistence boundary and PostgreSQL mapping layer."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import psycopg
from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactRelationship,
    ArtifactRelationshipKind,
    ArtifactVisibility,
)
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import ArtifactNotFound, InvalidUploadState
from .models import ArtifactEvent, ArtifactEventType, UploadSession, UploadStatus


class ArtifactRepository(Protocol):
    def create_upload(self, session: UploadSession, *, actor_id: UUID | None) -> None: ...

    def get_upload(self, upload_id: UUID) -> UploadSession | None: ...

    def mark_validating(self, upload_id: UUID, *, updated_at: datetime) -> UploadSession: ...

    def mark_validated(
        self,
        upload_id: UUID,
        *,
        sha256: str,
        media_type: str,
        byte_size: int,
        updated_at: datetime,
    ) -> UploadSession: ...

    def mark_failed(self, upload_id: UUID, reason: str, *, updated_at: datetime) -> None: ...

    def abort_upload(
        self,
        upload_id: UUID,
        *,
        actor_id: UUID | None,
        updated_at: datetime,
    ) -> UploadSession: ...

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
    ) -> tuple[ArtifactRecord, bool]: ...

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord | None: ...

    def storage_key(self, artifact_id: UUID) -> str | None: ...

    def record_access(
        self,
        artifact_id: UUID,
        *,
        actor_id: UUID | None,
        expires_in: int,
    ) -> None: ...

    def list_events(self, artifact_id: UUID) -> Sequence[ArtifactEvent]: ...


class PostgresArtifactRepository:
    """Explicit mapper; database rows are never reused as API or canonical contracts."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def create_upload(self, session: UploadSession, *, actor_id: UUID | None) -> None:
        relationships = [
            {
                "related_artifact_id": str(link.related_artifact_id),
                "kind": link.kind.value,
            }
            for link in session.relationships
        ]
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO artifact_uploads (
                    upload_id, backend_upload_id, staging_key, status, original_filename,
                    declared_media_type, expected_byte_size, source_uri, acquisition_method,
                    visibility, organization_id, relationships, created_at, updated_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    session.upload_id,
                    session.backend_upload_id,
                    session.staging_key,
                    session.status.value,
                    session.original_filename,
                    session.declared_media_type,
                    session.expected_byte_size,
                    session.source_uri,
                    session.acquisition_method.value,
                    session.visibility.value,
                    session.organization_id,
                    Jsonb(relationships),
                    session.created_at,
                    session.updated_at,
                    session.expires_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO artifact_events (upload_id, event_type, actor_id)
                VALUES (%s, %s, %s)
                """,
                (session.upload_id, ArtifactEventType.UPLOAD_INITIATED.value, actor_id),
            )

    def get_upload(self, upload_id: UUID) -> UploadSession | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM artifact_uploads WHERE upload_id = %s",
                (upload_id,),
            ).fetchone()
        return _upload_from_row(row) if row is not None else None

    def mark_validating(self, upload_id: UUID, *, updated_at: datetime) -> UploadSession:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM artifact_uploads WHERE upload_id = %s FOR UPDATE",
                (upload_id,),
            ).fetchone()
            if row is None:
                raise ArtifactNotFound(f"upload {upload_id} does not exist")
            status = UploadStatus(row["status"])
            if status is UploadStatus.PUBLISHED:
                return _upload_from_row(row)
            if status not in {UploadStatus.INITIATED, UploadStatus.VALIDATING}:
                raise InvalidUploadState(f"upload {upload_id} is {status.value}")
            row = connection.execute(
                """
                UPDATE artifact_uploads
                SET status = 'validating', updated_at = %s
                WHERE upload_id = %s
                RETURNING *
                """,
                (updated_at, upload_id),
            ).fetchone()
            assert row is not None
            return _upload_from_row(row)

    def mark_failed(self, upload_id: UUID, reason: str, *, updated_at: datetime) -> None:
        with psycopg.connect(self._database_url) as connection:
            changed = connection.execute(
                """
                UPDATE artifact_uploads
                SET status = 'failed', failure_reason = %s, updated_at = %s
                WHERE upload_id = %s AND status <> 'published'
                """,
                (reason[:2000], updated_at, upload_id),
            ).rowcount
            if changed:
                connection.execute(
                    """
                    INSERT INTO artifact_events (upload_id, event_type, details)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        upload_id,
                        ArtifactEventType.UPLOAD_FAILED.value,
                        Jsonb({"reason": reason[:2000]}),
                    ),
                )

    def mark_validated(
        self,
        upload_id: UUID,
        *,
        sha256: str,
        media_type: str,
        byte_size: int,
        updated_at: datetime,
    ) -> UploadSession:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE artifact_uploads
                SET validated_sha256 = %s,
                    validated_media_type = %s,
                    validated_byte_size = %s,
                    updated_at = %s
                WHERE upload_id = %s AND status = 'validating'
                RETURNING *
                """,
                (sha256, media_type, byte_size, updated_at, upload_id),
            ).fetchone()
            if row is None:
                raise InvalidUploadState(f"upload {upload_id} is not being validated")
            return _upload_from_row(row)

    def abort_upload(
        self,
        upload_id: UUID,
        *,
        actor_id: UUID | None,
        updated_at: datetime,
    ) -> UploadSession:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM artifact_uploads WHERE upload_id = %s FOR UPDATE",
                (upload_id,),
            ).fetchone()
            if row is None:
                raise ArtifactNotFound(f"upload {upload_id} does not exist")
            status = UploadStatus(row["status"])
            if status is UploadStatus.PUBLISHED:
                raise InvalidUploadState("a published upload cannot be aborted")
            if status is not UploadStatus.ABORTED:
                row = connection.execute(
                    """
                    UPDATE artifact_uploads
                    SET status = 'aborted', updated_at = %s
                    WHERE upload_id = %s
                    RETURNING *
                    """,
                    (updated_at, upload_id),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO artifact_events (upload_id, event_type, actor_id)
                    VALUES (%s, %s, %s)
                    """,
                    (upload_id, ArtifactEventType.UPLOAD_ABORTED.value, actor_id),
                )
            assert row is not None
            return _upload_from_row(row)

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
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            upload_row = connection.execute(
                "SELECT * FROM artifact_uploads WHERE upload_id = %s FOR UPDATE",
                (upload_id,),
            ).fetchone()
            if upload_row is None:
                raise ArtifactNotFound(f"upload {upload_id} does not exist")
            status = UploadStatus(upload_row["status"])
            if status is UploadStatus.PUBLISHED:
                artifact = self._get_artifact(connection, upload_row["artifact_id"])
                assert artifact is not None
                return artifact, True
            if status not in {UploadStatus.INITIATED, UploadStatus.VALIDATING}:
                raise InvalidUploadState(f"upload {upload_id} is {status.value}")

            connection.execute(
                """
                INSERT INTO artifact_blobs (sha256, media_type, byte_size, storage_key)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sha256) DO NOTHING
                """,
                (sha256, media_type, byte_size, storage_key),
            )
            blob = connection.execute(
                """
                SELECT media_type, byte_size, storage_key
                FROM artifact_blobs WHERE sha256 = %s
                """,
                (sha256,),
            ).fetchone()
            assert blob is not None
            if (
                blob["media_type"] != media_type
                or blob["byte_size"] != byte_size
                or blob["storage_key"] != storage_key
            ):
                raise ValueError("artifact blob metadata conflicts with its SHA-256 identity")

            candidate_artifact_id = uuid4()
            inserted = connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, blob_sha256, original_filename, source_uri,
                    acquisition_method, visibility, organization_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING artifact_id
                """,
                (
                    candidate_artifact_id,
                    sha256,
                    upload_row["original_filename"],
                    upload_row["source_uri"],
                    upload_row["acquisition_method"],
                    upload_row["visibility"],
                    upload_row["organization_id"],
                    updated_at,
                ),
            ).fetchone()
            deduplicated = inserted is None
            if inserted is not None:
                artifact_id = inserted["artifact_id"]
            else:
                artifact_row = connection.execute(
                    """
                    SELECT artifact_id
                    FROM artifacts
                    WHERE blob_sha256 = %s
                      AND visibility = %s
                      AND organization_id IS NOT DISTINCT FROM %s
                    """,
                    (sha256, upload_row["visibility"], upload_row["organization_id"]),
                ).fetchone()
                assert artifact_row is not None
                artifact_id = artifact_row["artifact_id"]

            for link in _relationships_from_json(upload_row["relationships"]):
                connection.execute(
                    """
                    INSERT INTO artifact_relationships (
                        artifact_id, related_artifact_id, relationship_kind
                    ) VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (artifact_id, link.related_artifact_id, link.kind.value),
                )

            connection.execute(
                """
                UPDATE artifact_uploads
                SET status = 'published', artifact_id = %s, updated_at = %s
                WHERE upload_id = %s
                """,
                (artifact_id, updated_at, upload_id),
            )
            connection.execute(
                """
                INSERT INTO artifact_events (
                    artifact_id, upload_id, event_type, actor_id, details
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    artifact_id,
                    upload_id,
                    (
                        ArtifactEventType.ARTIFACT_DEDUPLICATED.value
                        if deduplicated
                        else ArtifactEventType.ARTIFACT_CREATED.value
                    ),
                    actor_id,
                    Jsonb(
                        {
                            "sha256": sha256,
                            "original_filename": upload_row["original_filename"],
                            "source_uri": upload_row["source_uri"],
                            "acquisition_method": upload_row["acquisition_method"],
                        }
                    ),
                ),
            )
            artifact = self._get_artifact(connection, artifact_id)
            assert artifact is not None
            return artifact, deduplicated

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            return self._get_artifact(connection, artifact_id)

    def _get_artifact(self, connection: Any, artifact_id: UUID) -> ArtifactRecord | None:
        row = connection.execute(
            """
            SELECT a.artifact_id, a.original_filename, a.source_uri,
                   a.acquisition_method, a.visibility, a.organization_id, a.created_at,
                   b.sha256, b.media_type, b.byte_size
            FROM artifacts AS a
            JOIN artifact_blobs AS b ON b.sha256 = a.blob_sha256
            WHERE a.artifact_id = %s
            """,
            (artifact_id,),
        ).fetchone()
        if row is None:
            return None
        relationship_rows = connection.execute(
            """
            SELECT related_artifact_id, relationship_kind
            FROM artifact_relationships
            WHERE artifact_id = %s
            ORDER BY relationship_kind, related_artifact_id
            """,
            (artifact_id,),
        ).fetchall()
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            sha256=row["sha256"],
            media_type=row["media_type"],
            byte_size=row["byte_size"],
            original_filename=row["original_filename"],
            source_uri=row["source_uri"],
            acquisition_method=ArtifactAcquisitionMethod(row["acquisition_method"]),
            visibility=ArtifactVisibility(row["visibility"]),
            organization_id=row["organization_id"],
            relationships=tuple(
                ArtifactRelationship(
                    related_artifact_id=link["related_artifact_id"],
                    kind=ArtifactRelationshipKind(link["relationship_kind"]),
                )
                for link in relationship_rows
            ),
            created_at=row["created_at"],
        )

    def storage_key(self, artifact_id: UUID) -> str | None:
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                """
                SELECT b.storage_key
                FROM artifacts AS a
                JOIN artifact_blobs AS b ON b.sha256 = a.blob_sha256
                WHERE a.artifact_id = %s
                """,
                (artifact_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def record_access(
        self,
        artifact_id: UUID,
        *,
        actor_id: UUID | None,
        expires_in: int,
    ) -> None:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO artifact_events (artifact_id, event_type, actor_id, details)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    artifact_id,
                    ArtifactEventType.ACCESS_URL_CREATED.value,
                    actor_id,
                    Jsonb({"expires_in_seconds": expires_in}),
                ),
            )

    def list_events(self, artifact_id: UUID) -> Sequence[ArtifactEvent]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT event_id, artifact_id, upload_id, event_type, actor_id, details, created_at
                FROM artifact_events
                WHERE artifact_id = %s
                ORDER BY created_at, event_id
                """,
                (artifact_id,),
            ).fetchall()
        return tuple(
            ArtifactEvent(
                event_id=row["event_id"],
                artifact_id=row["artifact_id"],
                upload_id=row["upload_id"],
                event_type=ArtifactEventType(row["event_type"]),
                actor_id=row["actor_id"],
                details=cast(dict[str, Any], row["details"]),
                created_at=row["created_at"],
            )
            for row in rows
        )


def _relationships_from_json(value: object) -> tuple[ArtifactRelationship, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        ArtifactRelationship(
            related_artifact_id=UUID(str(item["related_artifact_id"])),
            kind=ArtifactRelationshipKind(str(item["kind"])),
        )
        for item in value
        if isinstance(item, dict)
    )


def _upload_from_row(row: dict[str, Any]) -> UploadSession:
    return UploadSession(
        upload_id=row["upload_id"],
        backend_upload_id=row["backend_upload_id"],
        staging_key=row["staging_key"],
        status=UploadStatus(row["status"]),
        original_filename=row["original_filename"],
        declared_media_type=row["declared_media_type"],
        expected_byte_size=row["expected_byte_size"],
        source_uri=row["source_uri"],
        acquisition_method=ArtifactAcquisitionMethod(row["acquisition_method"]),
        visibility=ArtifactVisibility(row["visibility"]),
        organization_id=row["organization_id"],
        relationships=_relationships_from_json(row["relationships"]),
        validated_sha256=row["validated_sha256"],
        validated_media_type=row["validated_media_type"],
        validated_byte_size=row["validated_byte_size"],
        artifact_id=row["artifact_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
    )
