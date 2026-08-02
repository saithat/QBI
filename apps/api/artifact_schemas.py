"""HTTP-only artifact schemas; storage contracts and persistence rows remain separate."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRelationshipKind,
    ArtifactVisibility,
    ContractModel,
)
from pydantic import Field, field_validator, model_validator


class ArtifactRelationshipInput(ContractModel):
    related_artifact_id: UUID
    kind: Literal["parent", "related", "derived_from", "supplement_to"]

    @field_validator("related_artifact_id", mode="before")
    @classmethod
    def json_related_artifact_id_to_uuid(cls, value: object) -> object:
        return UUID(value) if isinstance(value, str) else value


class BeginMultipartUploadRequest(ContractModel):
    original_filename: str = Field(min_length=1, max_length=1024)
    declared_media_type: str | None = Field(default=None, min_length=3, max_length=255)
    expected_byte_size: int | None = Field(default=None, ge=0)
    part_count: int = Field(ge=1, le=10_000)
    source_uri: str | None = Field(default=None, min_length=1, max_length=2048)
    visibility: Literal["public", "organization_private"]
    organization_id: UUID | None = None
    relationships: tuple[ArtifactRelationshipInput, ...] = ()

    @field_validator("relationships", mode="before")
    @classmethod
    def json_relationships_to_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("organization_id", mode="before")
    @classmethod
    def json_organization_id_to_uuid(cls, value: object) -> object:
        return UUID(value) if isinstance(value, str) else value

    @field_validator("original_filename")
    @classmethod
    def filename_must_be_safe_metadata(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("original_filename cannot be blank or contain NUL")
        return value

    @model_validator(mode="after")
    def visibility_scope_must_match(self) -> Self:
        if self.visibility == ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public uploads cannot include organization_id")
        if (
            self.visibility == ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private uploads require organization_id")
        return self


class UploadPartResponse(ContractModel):
    part_number: int = Field(ge=1, le=10_000)
    url: str = Field(min_length=1)


class BeginMultipartUploadResponse(ContractModel):
    upload_id: UUID
    expires_at: datetime
    parts: tuple[UploadPartResponse, ...]


class CompletedPartRequest(ContractModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=512)


class CompleteMultipartUploadRequest(ContractModel):
    parts: tuple[CompletedPartRequest, ...] = Field(min_length=1, max_length=10_000)

    @field_validator("parts", mode="before")
    @classmethod
    def json_parts_to_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class ArtifactRelationshipResponse(ContractModel):
    related_artifact_id: UUID
    kind: ArtifactRelationshipKind


class ArtifactResponse(ContractModel):
    artifact_id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str
    original_filename: str
    byte_size: int = Field(ge=0)
    source_uri: str | None
    acquisition_method: ArtifactAcquisitionMethod
    visibility: ArtifactVisibility
    organization_id: UUID | None
    relationships: tuple[ArtifactRelationshipResponse, ...]
    created_at: datetime


class ArtifactPublicationResponse(ContractModel):
    artifact: ArtifactResponse
    deduplicated: bool


class SourceIngestionRequest(ContractModel):
    adapter_name: Literal["http"]
    source_uri: str = Field(min_length=1, max_length=2048)
    visibility: Literal["public", "organization_private"]
    organization_id: UUID | None = None
    relationships: tuple[ArtifactRelationshipInput, ...] = ()

    @field_validator("relationships", mode="before")
    @classmethod
    def json_relationships_to_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("organization_id", mode="before")
    @classmethod
    def json_organization_id_to_uuid(cls, value: object) -> object:
        return UUID(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def visibility_scope_must_match(self) -> Self:
        if self.visibility == ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public ingestion cannot include organization_id")
        if (
            self.visibility == ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private ingestion requires organization_id")
        return self


class SignedArtifactUrlResponse(ContractModel):
    artifact_id: UUID
    url: str = Field(min_length=1)
    expires_at: datetime


class ArtifactEventResponse(ContractModel):
    event_id: int = Field(ge=1)
    artifact_id: UUID | None
    upload_id: UUID | None
    event_type: str
    actor_id: UUID | None
    details: dict[str, Any]
    created_at: datetime


class ArtifactEventListResponse(ContractModel):
    artifact_id: UUID
    events: tuple[ArtifactEventResponse, ...]
