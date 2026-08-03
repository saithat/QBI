"""Canonical public-source discovery and crawl-frontier contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Annotated, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, Field, StringConstraints, model_validator

from .artifacts import ArtifactReference
from .base import ContractModel, Identifier, MediaType, Sha256Digest
from .evaluation import ValidationIssue
from .identifiers import ToolIdentifier


def _validated_source_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("source URL must use HTTP(S) and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source URL cannot contain credentials")
    return value


type DiscoveryIdentityKey = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        pattern=r"^[a-z0-9][a-z0-9:._/-]{0,399}$",
    ),
]
type CanonicalSourceUrl = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=4096),
    AfterValidator(_validated_source_url),
]


class DiscoveryEntityKind(StrEnum):
    PAPER = "paper"
    SUPPLEMENTARY_ARCHIVE = "supplementary_archive"
    SOURCE_DATA_IMAGE = "source_data_image"
    METADATA_RECORD = "metadata_record"


class DiscoveryAcquisitionMethod(StrEnum):
    OFFICIAL_API = "official_api"
    BULK_MANIFEST = "bulk_manifest"
    ACCESSION_DOWNLOAD = "accession_download"
    OPEN_ACCESS_ARCHIVE = "open_access_archive"
    CONTROLLED_CRAWL = "controlled_crawl"


class DiscoveryAccessStatus(StrEnum):
    ALLOWED = "allowed"
    RESTRICTED = "restricted"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class DiscoveryLicenseStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"


class DiscoveryRobotsStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"
    ALLOWED = "allowed"
    PROHIBITED = "prohibited"


class CrawlFrontierStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    ACQUIRED = "acquired"
    RETRY_WAIT = "retry_wait"
    PROHIBITED = "prohibited"
    UNSUPPORTED = "unsupported"
    DEAD_LETTER = "dead_letter"


class DiscoveryRelationshipKind(StrEnum):
    PAPER_HAS_SUPPLEMENT = "paper_has_supplement"
    PAPER_HAS_SOURCE_DATA = "paper_has_source_data"
    DESCRIBES = "describes"
    ALTERNATIVE_REPRESENTATION = "alternative_representation"


class DiscoveryLicense(ContractModel):
    status: DiscoveryLicenseStatus
    identifier: Identifier | None = None
    label: str | None = Field(default=None, min_length=1, max_length=500)
    uri: CanonicalSourceUrl | None = None
    statement: str | None = Field(default=None, min_length=1, max_length=4000)
    verification_required: bool = True

    @model_validator(mode="after")
    def verified_license_must_be_identified(self) -> Self:
        if self.status is DiscoveryLicenseStatus.VERIFIED and not (self.identifier or self.uri):
            raise ValueError("a verified license requires an identifier or URI")
        if self.status is DiscoveryLicenseStatus.VERIFIED and self.verification_required:
            raise ValueError("a verified license cannot still require verification")
        return self


class DiscoveryQuery(ContractModel):
    from_date: date | None = None
    until_date: date | None = None
    cursor: str | None = Field(default=None, min_length=1, max_length=3000)
    maximum_pages: int = Field(default=20, ge=1, le=1000)

    @model_validator(mode="after")
    def date_range_is_ordered(self) -> Self:
        if (
            self.from_date is not None
            and self.until_date is not None
            and self.until_date < self.from_date
        ):
            raise ValueError("discovery until_date cannot precede from_date")
        if self.cursor is not None and (self.from_date is not None or self.until_date is not None):
            raise ValueError("a continuation cursor cannot be combined with a date range")
        return self


class DiscoveryRelationship(ContractModel):
    related_identity_key: DiscoveryIdentityKey
    kind: DiscoveryRelationshipKind


class DiscoveryRecord(ContractModel):
    identity_key: DiscoveryIdentityKey
    source_record_id: Identifier
    accession: Identifier | None = None
    entity_kind: DiscoveryEntityKind
    canonical_url: CanonicalSourceUrl
    acquisition_method: DiscoveryAcquisitionMethod
    priority: int = Field(default=100, ge=0, le=1000)
    source_updated_at: AwareDatetime | None = None
    discovered_at: AwareDatetime
    next_eligible_fetch_at: AwareDatetime
    robots_status: DiscoveryRobotsStatus
    expected_media_types: tuple[MediaType, ...] = ()
    license: DiscoveryLicense
    access_status: DiscoveryAccessStatus
    access_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    relationships: tuple[DiscoveryRelationship, ...] = ()
    trace_id: UUID

    @model_validator(mode="after")
    def discovery_shape_is_consistent(self) -> Self:
        if self.next_eligible_fetch_at < self.discovered_at:
            raise ValueError("next eligible fetch cannot precede discovery")
        if len(self.expected_media_types) != len(set(self.expected_media_types)):
            raise ValueError("expected media types must be unique")
        relationship_keys = [
            (relationship.related_identity_key, relationship.kind)
            for relationship in self.relationships
        ]
        if len(relationship_keys) != len(set(relationship_keys)):
            raise ValueError("discovery relationships must be unique")
        if any(
            relationship.related_identity_key == self.identity_key
            for relationship in self.relationships
        ):
            raise ValueError("a discovery record cannot relate to itself")
        if self.access_status is DiscoveryAccessStatus.PROHIBITED and not self.access_reason:
            raise ValueError("prohibited discovery records require an access reason")
        return self


class DiscoveryEvidence(ContractModel):
    request_url: CanonicalSourceUrl
    response_artifact: ArtifactReference
    response_sha256: Sha256Digest
    response_media_type: MediaType
    http_status: int = Field(ge=100, le=599)
    fetched_at: AwareDatetime

    @model_validator(mode="after")
    def response_artifact_matches_raw_response(self) -> Self:
        if self.response_artifact.sha256 != self.response_sha256:
            raise ValueError("response artifact hash must match the raw response hash")
        if self.response_artifact.media_type != self.response_media_type:
            raise ValueError("response artifact media type must match the raw response")
        return self


class DiscoveryBatch(ContractModel):
    batch_id: UUID
    source: ToolIdentifier
    query: DiscoveryQuery
    trace_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime
    records: tuple[DiscoveryRecord, ...]
    evidence: tuple[DiscoveryEvidence, ...]
    next_cursor: str | None = Field(default=None, min_length=1, max_length=3000)
    validation_issues: tuple[ValidationIssue, ...] = ()

    @model_validator(mode="after")
    def batch_is_consistent(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("discovery completion cannot precede start")
        identities = [record.identity_key for record in self.records]
        if len(identities) != len(set(identities)):
            raise ValueError("a discovery batch cannot repeat an identity key")
        if any(record.trace_id != self.trace_id for record in self.records):
            raise ValueError("discovery record trace IDs must match their batch")
        if self.records and not self.evidence:
            raise ValueError("a non-empty discovery batch requires response evidence")
        return self


class CrawlFrontierLease(ContractModel):
    owner: Identifier
    token: UUID
    expires_at: AwareDatetime


class CrawlFrontierRelationship(ContractModel):
    related_frontier_id: UUID
    related_identity_key: DiscoveryIdentityKey
    kind: DiscoveryRelationshipKind
    created_at: AwareDatetime


class DiscoveryArtifactAcquisition(ContractModel):
    artifact: ArtifactReference
    trace_id: UUID
    acquired_at: AwareDatetime


class CrawlFrontierRecord(ContractModel):
    frontier_id: UUID
    identity_key: DiscoveryIdentityKey
    primary_source: ToolIdentifier
    source_record_id: Identifier
    accession: Identifier | None = None
    entity_kind: DiscoveryEntityKind
    canonical_url: CanonicalSourceUrl
    acquisition_method: DiscoveryAcquisitionMethod
    status: CrawlFrontierStatus
    priority: int = Field(ge=0, le=1000)
    source_updated_at: AwareDatetime | None = None
    discovered_at: AwareDatetime
    last_discovered_at: AwareDatetime
    next_eligible_fetch_at: AwareDatetime
    attempt_count: int = Field(ge=0)
    lease: CrawlFrontierLease | None = None
    robots_status: DiscoveryRobotsStatus
    expected_media_types: tuple[MediaType, ...] = ()
    license: DiscoveryLicense
    access_status: DiscoveryAccessStatus
    access_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    trace_id: UUID
    last_discovery_batch_id: UUID
    relationships: tuple[CrawlFrontierRelationship, ...] = ()
    acquisitions: tuple[DiscoveryArtifactAcquisition, ...] = ()
    version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def frontier_state_is_consistent(self) -> Self:
        if self.last_discovered_at < self.discovered_at:
            raise ValueError("last discovery cannot precede first discovery")
        if self.updated_at < self.created_at:
            raise ValueError("frontier update cannot precede creation")
        if (self.status is CrawlFrontierStatus.LEASED) != (self.lease is not None):
            raise ValueError("only leased frontier records may contain a lease")
        if self.status is CrawlFrontierStatus.ACQUIRED and not self.acquisitions:
            raise ValueError("acquired frontier records require an artifact acquisition")
        if self.status is CrawlFrontierStatus.PROHIBITED:
            if self.access_status is not DiscoveryAccessStatus.PROHIBITED:
                raise ValueError("prohibited frontier status requires prohibited access")
            if not self.access_reason:
                raise ValueError("prohibited frontier records require an access reason")
        if self.access_status is DiscoveryAccessStatus.PROHIBITED and (
            self.status is not CrawlFrontierStatus.PROHIBITED
        ):
            raise ValueError("prohibited access must produce prohibited frontier status")
        if len(self.expected_media_types) != len(set(self.expected_media_types)):
            raise ValueError("expected media types must be unique")
        if any(
            relationship.related_frontier_id == self.frontier_id
            or relationship.related_identity_key == self.identity_key
            for relationship in self.relationships
        ):
            raise ValueError("a frontier record cannot relate to itself")
        return self


class CrawlFrontierFilters(ContractModel):
    status: CrawlFrontierStatus | None = None
    entity_kind: DiscoveryEntityKind | None = None
    source_name: Identifier | None = None
    access_status: DiscoveryAccessStatus | None = None
    missing_artifact: bool | None = None


class CrawlFrontierPage(ContractModel):
    filters: CrawlFrontierFilters
    items: tuple[CrawlFrontierRecord, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class DiscoveryIngestionResult(ContractModel):
    batch_id: UUID
    created_count: int = Field(ge=0)
    deduplicated_count: int = Field(ge=0)
    records: tuple[CrawlFrontierRecord, ...]

    @model_validator(mode="after")
    def counts_match_records(self) -> Self:
        if self.created_count + self.deduplicated_count != len(self.records):
            raise ValueError("discovery ingestion counts must match records")
        return self


def discovery_batch_sha256(batch: DiscoveryBatch) -> str:
    """Return a deterministic digest for idempotent discovery-run persistence."""

    payload = json.dumps(
        batch.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
