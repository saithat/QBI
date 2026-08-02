"""Storage-domain state, deliberately separate from API and database row models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRelationship,
    ArtifactVisibility,
)


class UploadStatus(StrEnum):
    INITIATED = "initiated"
    VALIDATING = "validating"
    PUBLISHED = "published"
    FAILED = "failed"
    ABORTED = "aborted"


class ArtifactEventType(StrEnum):
    UPLOAD_INITIATED = "upload_initiated"
    UPLOAD_ABORTED = "upload_aborted"
    UPLOAD_FAILED = "upload_failed"
    ARTIFACT_CREATED = "artifact_created"
    ARTIFACT_DEDUPLICATED = "artifact_deduplicated"
    ACCESS_URL_CREATED = "access_url_created"


@dataclass(frozen=True, slots=True)
class CompletedPart:
    part_number: int
    etag: str


@dataclass(frozen=True, slots=True)
class UploadSession:
    upload_id: UUID
    backend_upload_id: str | None
    staging_key: str
    status: UploadStatus
    original_filename: str
    declared_media_type: str | None
    expected_byte_size: int | None
    source_uri: str | None
    acquisition_method: ArtifactAcquisitionMethod
    visibility: ArtifactVisibility
    organization_id: UUID | None
    relationships: tuple[ArtifactRelationship, ...]
    validated_sha256: str | None
    validated_media_type: str | None
    validated_byte_size: int | None
    artifact_id: UUID | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ArtifactEvent:
    event_id: int
    artifact_id: UUID | None
    upload_id: UUID | None
    event_type: ArtifactEventType
    actor_id: UUID | None
    details: dict[str, Any]
    created_at: datetime
