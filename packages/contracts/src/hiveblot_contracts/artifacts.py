"""Artifact, source-document, and source-coordinate contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .base import ContractModel, Identifier, MediaType, Sha256Digest


class ArtifactReference(ContractModel):
    """Stable metadata needed to identify immutable bytes without embedding them."""

    artifact_id: UUID
    sha256: Sha256Digest
    media_type: MediaType
    byte_size: int = Field(ge=0)


class ArtifactVisibility(StrEnum):
    """Visibility attached to artifact metadata and access grants."""

    PUBLIC = "public"
    ORGANIZATION_PRIVATE = "organization_private"


class ArtifactAcquisitionMethod(StrEnum):
    """How immutable bytes entered HiveBlot."""

    USER_UPLOAD = "user_upload"
    SOURCE_ADAPTER = "source_adapter"
    TOOL_OUTPUT = "tool_output"


class ArtifactRelationshipKind(StrEnum):
    """Directed provenance relationship between two artifacts."""

    PARENT = "parent"
    RELATED = "related"
    DERIVED_FROM = "derived_from"
    SUPPLEMENT_TO = "supplement_to"


class ArtifactRelationship(ContractModel):
    """A stable link from an artifact to another immutable artifact."""

    related_artifact_id: UUID
    kind: ArtifactRelationshipKind


class ArtifactRecord(ArtifactReference):
    """Published metadata for immutable, content-addressed scientific bytes."""

    original_filename: str = Field(min_length=1, max_length=1024)
    source_uri: str | None = Field(default=None, min_length=1, max_length=2048)
    acquisition_method: ArtifactAcquisitionMethod
    visibility: ArtifactVisibility
    organization_id: UUID | None = None
    relationships: tuple[ArtifactRelationship, ...] = ()
    created_at: AwareDatetime

    @model_validator(mode="after")
    def visibility_scope_must_be_consistent(self) -> Self:
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public artifacts cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private artifacts require an organization_id")
        return self


class SourceDocumentKind(StrEnum):
    PDF = "pdf"
    IMAGE = "image"
    SUPPLEMENTARY_ARCHIVE = "supplementary_archive"
    JSON_METADATA = "json_metadata"
    TABULAR_METADATA = "tabular_metadata"


class SourceDocumentRecord(ContractModel):
    """Canonical identity and provenance for one acquired scientific document."""

    document_id: UUID
    artifact: ArtifactReference
    kind: SourceDocumentKind
    source_uri: str | None = Field(default=None, min_length=1, max_length=2048)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    external_identifier: Identifier | None = None
    acquired_at: AwareDatetime


class BoundingRegion(ContractModel):
    """A rectangle expressed in immutable source-image pixel coordinates."""

    region_id: UUID
    source_artifact_id: UUID
    coordinate_space: Literal["source_pixels"] = "source_pixels"
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    canvas_width: int = Field(gt=0)
    canvas_height: int = Field(gt=0)
    page_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def region_must_fit_source_canvas(self) -> Self:
        if self.x + self.width > self.canvas_width:
            raise ValueError("region exceeds source canvas width")
        if self.y + self.height > self.canvas_height:
            raise ValueError("region exceeds source canvas height")
        return self
