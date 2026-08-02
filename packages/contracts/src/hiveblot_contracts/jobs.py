"""Domain-independent queue contracts for finite computational work."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .artifacts import ArtifactReference
from .base import ContractModel, Identifier, MediaType
from .evaluation import ValidationIssue
from .identifiers import PipelineIdentifier


class NetworkPolicy(StrEnum):
    DENY = "deny"
    ALLOW = "allow"


class ResourceRequirements(ContractModel):
    cpu_millicores: int = Field(gt=0)
    memory_mib: int = Field(gt=0)
    gpu_count: int = Field(default=0, ge=0)


class ContainerSpecification(ContractModel):
    image: str = Field(min_length=1, max_length=1000)
    command: tuple[Identifier, ...] = Field(min_length=1)
    arguments: tuple[str, ...] = ()
    allowed_environment_variables: tuple[Identifier, ...] = ()
    network_policy: NetworkPolicy = NetworkPolicy.DENY


class NamedArtifactReference(ContractModel):
    name: Identifier
    artifact: ArtifactReference


class ExpectedJobOutput(ContractModel):
    name: Identifier
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
    parent_job_id: UUID | None = None
    submitted_at: AwareDatetime
    timeout_seconds: int = Field(gt=0, le=604800)
    max_attempts: int = Field(default=1, ge=1, le=100)


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


def duration_milliseconds(started_at: datetime, completed_at: datetime) -> int:
    """Return an exact integer duration for persistence and tracing adapters."""

    return int((completed_at - started_at).total_seconds() * 1000)
