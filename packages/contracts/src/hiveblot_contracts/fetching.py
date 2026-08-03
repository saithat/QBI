"""Strict contracts for distributed public-source fetching and ingestion handoff."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from .artifacts import ArtifactReference
from .base import ContractModel, Identifier, MediaType, Sha256Digest
from .discovery import CanonicalSourceUrl, DiscoveryAcquisitionMethod

type DomainName = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    ),
]


class CrawlFetchTaskStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    NOT_MODIFIED = "not_modified"
    PROHIBITED = "prohibited"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class CrawlFetchAttemptOutcome(StrEnum):
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    NOT_MODIFIED = "not_modified"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    PROHIBITED = "prohibited"
    LEASE_EXPIRED = "lease_expired"
    CANCELLED = "cancelled"


class DownstreamIngestionKind(StrEnum):
    PARSE = "parse"
    EXTRACT = "extract"


class DownstreamIngestionStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class CrawlFetchError(ContractModel):
    code: Identifier
    message: str = Field(min_length=1, max_length=4000)
    retryable: bool


class CrawlFetchLease(ContractModel):
    task_id: UUID
    frontier_id: UUID
    attempt_id: UUID
    attempt: int = Field(ge=1)
    worker_id: Identifier
    lease_token: UUID
    canonical_url: CanonicalSourceUrl
    domain: DomainName
    acquisition_method: DiscoveryAcquisitionMethod
    expected_media_types: tuple[MediaType, ...]
    trace_id: UUID
    etag: str | None = Field(default=None, min_length=1, max_length=1000)
    last_modified: str | None = Field(default=None, min_length=1, max_length=1000)
    leased_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def lease_is_valid(self) -> Self:
        if self.expires_at <= self.leased_at:
            raise ValueError("fetch lease expiration must follow acquisition")
        if not self.expected_media_types:
            raise ValueError("fetch leases require at least one expected media type")
        if len(self.expected_media_types) != len(set(self.expected_media_types)):
            raise ValueError("fetch lease media types must be unique")
        return self


class CrawlFetchAttemptRecord(ContractModel):
    attempt_id: UUID
    task_id: UUID
    frontier_id: UUID
    attempt: int = Field(ge=1)
    worker_id: Identifier
    trace_id: UUID
    outcome: CrawlFetchAttemptOutcome
    request_url: CanonicalSourceUrl
    final_url: CanonicalSourceUrl | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    response_media_type: MediaType | None = None
    response_sha256: Sha256Digest | None = None
    bytes_downloaded: int | None = Field(default=None, ge=0)
    latency_milliseconds: int | None = Field(default=None, ge=0)
    artifact: ArtifactReference | None = None
    artifact_deduplicated: bool | None = None
    retry_after: AwareDatetime | None = None
    error: CrawlFetchError | None = None
    leased_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def attempt_is_consistent(self) -> Self:
        terminal = self.outcome is not CrawlFetchAttemptOutcome.LEASED
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal fetch attempts require a completion time")
        if self.started_at is not None and self.started_at < self.leased_at:
            raise ValueError("fetch attempt cannot start before its lease")
        if self.completed_at is not None:
            start = self.started_at or self.leased_at
            if self.completed_at < start:
                raise ValueError("fetch attempt completion cannot precede its start")
        if self.artifact is not None:
            if self.outcome is not CrawlFetchAttemptOutcome.SUCCEEDED:
                raise ValueError("only successful fetches may publish an artifact")
            if self.response_sha256 != self.artifact.sha256:
                raise ValueError("fetch artifact hash must match the response hash")
            if self.response_media_type != self.artifact.media_type:
                raise ValueError("fetch artifact media type must match the response")
            if self.bytes_downloaded != self.artifact.byte_size:
                raise ValueError("fetch artifact byte size must match the response")
        if (
            self.outcome
            in {
                CrawlFetchAttemptOutcome.RETRYABLE_FAILURE,
                CrawlFetchAttemptOutcome.PERMANENT_FAILURE,
                CrawlFetchAttemptOutcome.PROHIBITED,
            }
            and self.error is None
        ):
            raise ValueError("failed fetch attempts require an error")
        return self


class CrawlFetchTaskRecord(ContractModel):
    task_id: UUID
    frontier_id: UUID
    canonical_url: CanonicalSourceUrl
    domain: DomainName
    acquisition_method: DiscoveryAcquisitionMethod
    expected_media_types: tuple[MediaType, ...]
    status: CrawlFetchTaskStatus
    priority: int = Field(ge=0, le=1000)
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1, le=100)
    next_eligible_at: AwareDatetime
    trace_id: UUID
    lease: CrawlFetchLease | None = None
    result_artifact: ArtifactReference | None = None
    etag: str | None = Field(default=None, min_length=1, max_length=1000)
    last_modified: str | None = Field(default=None, min_length=1, max_length=1000)
    last_error: CrawlFetchError | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def task_is_consistent(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("fetch task update cannot precede creation")
        if (self.status is CrawlFetchTaskStatus.LEASED) != (self.lease is not None):
            raise ValueError("only leased fetch tasks may contain a lease")
        terminal = self.status in {
            CrawlFetchTaskStatus.SUCCEEDED,
            CrawlFetchTaskStatus.NOT_MODIFIED,
            CrawlFetchTaskStatus.PROHIBITED,
            CrawlFetchTaskStatus.DEAD_LETTER,
            CrawlFetchTaskStatus.CANCELLED,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal fetch tasks require a completion time")
        if self.status is CrawlFetchTaskStatus.SUCCEEDED and self.result_artifact is None:
            raise ValueError("successful fetch tasks require a result artifact")
        if self.status is not CrawlFetchTaskStatus.SUCCEEDED and self.result_artifact is not None:
            raise ValueError("only successful fetch tasks may publish a result artifact")
        if len(self.expected_media_types) != len(set(self.expected_media_types)):
            raise ValueError("fetch task media types must be unique")
        return self


class DomainRatePolicy(ContractModel):
    domain: DomainName
    minimum_interval_milliseconds: int = Field(ge=0, le=3_600_000)
    maximum_concurrency: int = Field(ge=1, le=1000)
    next_request_at: AwareDatetime
    updated_at: AwareDatetime


class DomainRequestPermit(ContractModel):
    permit_id: UUID
    domain: DomainName
    worker_id: Identifier
    acquired_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def permit_is_valid(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("domain permit expiration must follow acquisition")
        return self


class DownstreamIngestionTask(ContractModel):
    downstream_task_id: UUID
    fetch_task_id: UUID
    frontier_id: UUID
    kind: DownstreamIngestionKind
    status: DownstreamIngestionStatus
    artifact: ArtifactReference
    trace_id: UUID
    attempt_count: int = Field(ge=0)
    created_at: AwareDatetime
    published_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def publication_is_consistent(self) -> Self:
        if (self.status is DownstreamIngestionStatus.PUBLISHED) != (self.published_at is not None):
            raise ValueError("published downstream tasks require a publication time")
        return self


class CrawlMetricsSnapshot(ContractModel):
    measured_at: AwareDatetime
    queue_depth: int = Field(ge=0)
    leased_tasks: int = Field(ge=0)
    active_workers: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    dead_letter_count: int = Field(ge=0)
    completed_fetches: int = Field(ge=0)
    bytes_downloaded: int = Field(ge=0)
    deduplicated_artifacts: int = Field(ge=0)
    fetch_latency_milliseconds_sum: int = Field(ge=0)
    http_status_counts: dict[str, int] = Field(default_factory=dict)
    source_error_counts: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def metric_maps_are_non_negative(self) -> Self:
        if any(value < 0 for value in self.http_status_counts.values()):
            raise ValueError("HTTP status counts cannot be negative")
        if any(value < 0 for value in self.source_error_counts.values()):
            raise ValueError("source error counts cannot be negative")
        return self
