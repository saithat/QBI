"""Domain-independent queue contracts for finite computational work."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from .artifacts import ArtifactReference, ArtifactVisibility
from .base import ContractModel, Identifier, MediaType
from .evaluation import ValidationIssue
from .identifiers import PipelineIdentifier

type JobArtifactName = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        pattern=r"^[a-z][a-z0-9_.-]{0,127}$",
    ),
]
type EnvironmentVariableName = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[A-Z_][A-Z0-9_]{0,127}$"),
]


class NetworkPolicy(StrEnum):
    DENY = "deny"
    ALLOW = "allow"


class JobStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class JobAttemptStatus(StrEnum):
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LEASE_EXPIRED = "lease_expired"


class JobLogStream(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"


class ResourceRequirements(ContractModel):
    cpu_millicores: int = Field(gt=0)
    memory_mib: int = Field(gt=0)
    gpu_count: int = Field(default=0, ge=0)


class ContainerSpecification(ContractModel):
    image: str = Field(min_length=1, max_length=1000)
    command: tuple[Identifier, ...] = Field(min_length=1)
    arguments: tuple[str, ...] = ()
    allowed_environment_variables: tuple[EnvironmentVariableName, ...] = ()
    network_policy: NetworkPolicy = NetworkPolicy.DENY

    @model_validator(mode="after")
    def environment_names_are_unique(self) -> Self:
        if len(self.allowed_environment_variables) != len(set(self.allowed_environment_variables)):
            raise ValueError("allowed environment-variable names must be unique")
        if any(character.isspace() for character in self.image):
            raise ValueError("container image must not contain whitespace")
        return self


class NamedArtifactReference(ContractModel):
    name: JobArtifactName
    artifact: ArtifactReference


class ExpectedJobOutput(ContractModel):
    name: JobArtifactName
    media_type: MediaType
    required: bool = True


class JobSpecification(ContractModel):
    message_type: Literal["job_specification"] = "job_specification"
    job_id: UUID
    job_type: Identifier
    idempotency_key: Identifier
    pipeline: PipelineIdentifier
    container: ContainerSpecification
    resources: ResourceRequirements
    inputs: tuple[NamedArtifactReference, ...] = ()
    expected_outputs: tuple[ExpectedJobOutput, ...] = ()
    trace_id: UUID
    visibility: ArtifactVisibility = ArtifactVisibility.PUBLIC
    organization_id: UUID | None = None
    submitted_by: UUID | None = None
    parent_job_id: UUID | None = None
    submitted_at: AwareDatetime
    timeout_seconds: int = Field(gt=0, le=604800)
    max_attempts: int = Field(default=1, ge=1, le=100)
    retry_initial_backoff_seconds: int = Field(default=5, ge=0, le=86_400)
    retry_backoff_multiplier: float = Field(default=2.0, ge=1, le=100)
    retry_max_backoff_seconds: int = Field(default=300, ge=0, le=604_800)

    @model_validator(mode="after")
    def names_and_retry_policy_are_consistent(self) -> Self:
        input_names = [item.name for item in self.inputs]
        output_names = [item.name for item in self.expected_outputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("job input names must be unique")
        if len(output_names) != len(set(output_names)):
            raise ValueError("expected job output names must be unique")
        if self.expected_outputs and not self.inputs:
            raise ValueError("output-producing jobs require at least one provenance input")
        if self.retry_initial_backoff_seconds > self.retry_max_backoff_seconds:
            raise ValueError("initial retry backoff cannot exceed maximum retry backoff")
        if self.parent_job_id == self.job_id:
            raise ValueError("a job cannot be its own parent")
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public jobs cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private jobs require an organization")
        return self


class JobError(ContractModel):
    code: Identifier
    message: str = Field(min_length=1, max_length=8000)
    retryable: bool


class _TerminalJobResult(ContractModel):
    message_type: Literal["job_result"] = "job_result"
    job_id: UUID
    attempt: int = Field(ge=1)
    trace_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime
    outputs: tuple[NamedArtifactReference, ...] = ()
    validation_issues: tuple[ValidationIssue, ...] = ()
    logs_artifact: ArtifactReference | None = None

    @model_validator(mode="after")
    def completion_must_follow_start(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        return self


class SucceededJobResult(_TerminalJobResult):
    status: Literal["succeeded"] = "succeeded"


class FailedJobResult(_TerminalJobResult):
    status: Literal["failed"] = "failed"
    error: JobError


class CancelledJobResult(_TerminalJobResult):
    status: Literal["cancelled"] = "cancelled"
    reason: str = Field(min_length=1, max_length=4000)


type JobResult = Annotated[
    SucceededJobResult | FailedJobResult | CancelledJobResult,
    Field(discriminator="status"),
]


class JobLease(ContractModel):
    message_type: Literal["job_lease"] = "job_lease"
    lease_token: UUID
    job_id: UUID
    attempt_id: UUID
    attempt: int = Field(ge=1)
    worker_id: Identifier
    specification: JobSpecification
    leased_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def lease_matches_specification(self) -> Self:
        if self.job_id != self.specification.job_id:
            raise ValueError("lease job ID must match its specification")
        if self.expires_at <= self.leased_at:
            raise ValueError("lease expiration must follow lease creation")
        return self


class JobRecord(ContractModel):
    job_id: UUID
    specification: JobSpecification
    status: JobStatus
    attempt_count: int = Field(ge=0)
    cancel_requested: bool
    cancellation_reason: str | None = Field(default=None, min_length=1, max_length=4000)
    current_worker_id: str | None = Field(default=None, min_length=1, max_length=200)
    lease_expires_at: AwareDatetime | None = None
    result: JobResult | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def state_is_consistent(self) -> Self:
        if self.job_id != self.specification.job_id:
            raise ValueError("job record ID must match its specification")
        if self.updated_at < self.created_at:
            raise ValueError("job update cannot precede creation")
        terminal = {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.DEAD_LETTER,
        }
        if (self.status in terminal) != (self.completed_at is not None):
            raise ValueError("terminal job status and completion timestamp must agree")
        if self.status in {JobStatus.LEASED, JobStatus.RUNNING}:
            if self.current_worker_id is None or self.lease_expires_at is None:
                raise ValueError("leased jobs require a worker and expiration")
        elif self.current_worker_id is not None or self.lease_expires_at is not None:
            raise ValueError("non-leased jobs cannot expose an active worker lease")
        if self.result is not None:
            if (
                self.result.job_id != self.job_id
                or self.result.trace_id != self.specification.trace_id
            ):
                raise ValueError("job result identity must match its specification")
            expected_statuses = {
                "succeeded": {JobStatus.SUCCEEDED},
                "failed": {JobStatus.FAILED, JobStatus.DEAD_LETTER},
                "cancelled": {JobStatus.CANCELLED},
            }[self.result.status]
            if self.status not in expected_statuses:
                raise ValueError("terminal job result must match the job status")
        return self


class JobAttemptRecord(ContractModel):
    attempt_id: UUID
    job_id: UUID
    attempt: int = Field(ge=1)
    worker_id: Identifier
    status: JobAttemptStatus
    executor_name: Identifier
    leased_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    lease_expires_at: AwareDatetime
    result: JobResult | None = None

    @model_validator(mode="after")
    def attempt_timestamps_are_ordered(self) -> Self:
        if self.started_at is not None and self.started_at < self.leased_at:
            raise ValueError("attempt start cannot precede its lease")
        if self.completed_at is not None:
            start = self.started_at or self.leased_at
            if self.completed_at < start:
                raise ValueError("attempt completion cannot precede its start")
        terminal = {
            JobAttemptStatus.SUCCEEDED,
            JobAttemptStatus.FAILED,
            JobAttemptStatus.CANCELLED,
            JobAttemptStatus.LEASE_EXPIRED,
        }
        if (self.status in terminal) != (self.completed_at is not None):
            raise ValueError("terminal attempt status and completion timestamp must agree")
        if self.result is not None and (
            self.result.job_id != self.job_id or self.result.attempt != self.attempt
        ):
            raise ValueError("attempt result identity must match its attempt")
        return self


class JobLogRecord(ContractModel):
    log_id: UUID
    attempt_id: UUID
    sequence: int = Field(ge=1)
    stream: JobLogStream
    content: str = Field(max_length=262_144)
    truncated: bool = False
    created_at: AwareDatetime


def duration_milliseconds(started_at: datetime, completed_at: datetime) -> int:
    """Return an exact integer duration for persistence and tracing adapters."""

    return int((completed_at - started_at).total_seconds() * 1000)
