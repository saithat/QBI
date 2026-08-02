"""Durable, domain-independent job lifecycle and lease orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactRecord,
    ArtifactReference,
    FailedJobResult,
    JobAttemptRecord,
    JobLease,
    JobLogRecord,
    JobLogStream,
    JobRecord,
    JobResult,
    JobSpecification,
    JobStatus,
    SucceededJobResult,
)

from .errors import InvalidJobState, JobNotFound
from .repository import JobRepository


class JobArtifactLookup(Protocol):
    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord: ...


class JobService:
    def __init__(
        self,
        repository: JobRepository,
        artifacts: JobArtifactLookup,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts
        self._clock = clock or (lambda: datetime.now(UTC))

    def submit(self, specification: JobSpecification) -> JobRecord:
        if specification.parent_job_id is not None:
            self.get_job(specification.parent_job_id)
        for named in specification.inputs:
            canonical = self._artifact_reference(named.artifact.artifact_id)
            if named.artifact != canonical:
                raise InvalidJobState(
                    f"job input {named.name!r} does not match canonical artifact metadata"
                )
        rendered = _json(specification)
        return self._repository.submit(
            specification,
            specification_sha256=hashlib.sha256(rendered.encode()).hexdigest(),
            created_at=self._clock(),
        )

    def get_job(self, job_id: UUID) -> JobRecord:
        record = self._repository.get_job(job_id)
        if record is None:
            raise JobNotFound(f"job {job_id} does not exist")
        return record

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[JobRecord]:
        return self._repository.list_jobs(
            status=status,
            limit=max(1, min(limit, 200)),
            offset=max(0, offset),
        )

    def lease_next(
        self,
        *,
        worker_id: str,
        executor_name: str,
        lease_seconds: int,
    ) -> JobLease | None:
        if not 5 <= lease_seconds <= 3600:
            raise InvalidJobState("lease_seconds must be between 5 and 3600")
        now = self._clock()
        self._repository.expire_leases(expired_at=now)
        return self._repository.lease_next(
            worker_id=worker_id,
            executor_name=executor_name,
            leased_at=now,
            expires_at=now + timedelta(seconds=lease_seconds),
        )

    def mark_running(self, lease_token: UUID) -> JobAttemptRecord:
        return self._repository.mark_running(lease_token, started_at=self._clock())

    def heartbeat(self, lease_token: UUID, *, lease_seconds: int) -> JobLease:
        if not 5 <= lease_seconds <= 3600:
            raise InvalidJobState("lease_seconds must be between 5 and 3600")
        now = self._clock()
        return self._repository.heartbeat(
            lease_token,
            heartbeat_at=now,
            expires_at=now + timedelta(seconds=lease_seconds),
        )

    def cancel(self, job_id: UUID, *, reason: str) -> JobRecord:
        reason = reason.strip()
        if not reason:
            raise InvalidJobState("cancellation reason is required")
        return self._repository.request_cancel(
            job_id,
            reason=reason[:4000],
            cancelled_at=self._clock(),
        )

    def is_cancel_requested(self, job_id: UUID) -> bool:
        return self._repository.is_cancel_requested(job_id)

    def publish_result(self, lease_token: UUID, result: JobResult) -> JobRecord:
        lease = self._repository.get_lease(lease_token)
        if lease is None:
            raise JobNotFound(f"job lease {lease_token} does not exist")
        self._validate_result(lease, result)
        next_status = self._next_status(lease, result)
        completed_at = self._clock()
        next_eligible_at = completed_at
        if next_status is JobStatus.PENDING:
            next_eligible_at += timedelta(
                seconds=_retry_delay_seconds(lease.specification, lease.attempt)
            )
        return self._repository.complete(
            lease_token,
            result=result,
            next_status=next_status,
            next_eligible_at=next_eligible_at,
            completed_at=completed_at,
        )

    def append_log(
        self,
        attempt_id: UUID,
        *,
        sequence: int,
        stream: JobLogStream,
        content: str,
        truncated: bool,
    ) -> JobLogRecord:
        return self._repository.append_log(
            JobLogRecord(
                log_id=uuid4(),
                attempt_id=attempt_id,
                sequence=sequence,
                stream=stream,
                content=content[:262_144],
                truncated=truncated or len(content) > 262_144,
                created_at=self._clock(),
            )
        )

    def list_attempts(self, job_id: UUID) -> Sequence[JobAttemptRecord]:
        self.get_job(job_id)
        return self._repository.list_attempts(job_id)

    def list_logs(self, attempt_id: UUID) -> Sequence[JobLogRecord]:
        return self._repository.list_logs(attempt_id)

    def reap_expired_leases(self) -> Sequence[JobRecord]:
        return self._repository.expire_leases(expired_at=self._clock())

    def _validate_result(self, lease: JobLease, result: JobResult) -> None:
        if (
            result.job_id != lease.job_id
            or result.attempt != lease.attempt
            or result.trace_id != lease.specification.trace_id
        ):
            raise InvalidJobState("job result identity does not match its lease")
        names = [item.name for item in result.outputs]
        if len(names) != len(set(names)):
            raise InvalidJobState("job result output names must be unique")
        expected = {item.name: item for item in lease.specification.expected_outputs}
        unknown = set(names) - expected.keys()
        if unknown:
            raise InvalidJobState("job result includes an undeclared output")
        if isinstance(result, SucceededJobResult):
            missing = {name for name, item in expected.items() if item.required} - set(names)
            if missing:
                raise InvalidJobState("successful job result is missing a required output")
        for named in result.outputs:
            canonical = self._artifact_reference(named.artifact.artifact_id)
            if canonical != named.artifact:
                raise InvalidJobState("job output does not match canonical artifact metadata")
            if canonical.media_type != expected[named.name].media_type:
                raise InvalidJobState("job output media type does not match its declaration")
        if result.logs_artifact is not None and result.logs_artifact != self._artifact_reference(
            result.logs_artifact.artifact_id
        ):
            raise InvalidJobState("job logs artifact metadata is not canonical")

    def _next_status(self, lease: JobLease, result: JobResult) -> JobStatus:
        cancel_requested = self._repository.is_cancel_requested(lease.job_id)
        if cancel_requested and result.status != "cancelled":
            if result.status == "succeeded":
                raise InvalidJobState("a cancelled job cannot publish success")
            raise InvalidJobState("a cancelled job requires a cancelled result")
        if not cancel_requested and result.status == "cancelled":
            raise InvalidJobState(
                "a job without a cancellation request cannot publish cancellation"
            )
        if result.status == "succeeded":
            return JobStatus.SUCCEEDED
        if result.status == "cancelled":
            return JobStatus.CANCELLED
        if not isinstance(result, FailedJobResult):  # pragma: no cover - discriminated contract
            raise AssertionError("unknown terminal job result")
        if not result.error.retryable:
            return JobStatus.FAILED
        if lease.attempt >= lease.specification.max_attempts:
            return JobStatus.DEAD_LETTER
        return JobStatus.PENDING

    def _artifact_reference(self, artifact_id: UUID) -> ArtifactReference:
        artifact = self._artifacts.get_artifact(artifact_id)
        return ArtifactReference(
            artifact_id=artifact.artifact_id,
            sha256=artifact.sha256,
            media_type=artifact.media_type,
            byte_size=artifact.byte_size,
        )


def _retry_delay_seconds(specification: JobSpecification, attempt: int) -> float:
    return min(
        specification.retry_max_backoff_seconds,
        specification.retry_initial_backoff_seconds
        * specification.retry_backoff_multiplier ** max(0, attempt - 1),
    )


def _json(specification: JobSpecification) -> str:
    return json.dumps(
        specification.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
