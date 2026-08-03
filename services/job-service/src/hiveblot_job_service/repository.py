"""Persistence boundary for durable generic jobs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from hiveblot_contracts import (
    JobAttemptRecord,
    JobLease,
    JobLogRecord,
    JobRecord,
    JobResult,
    JobSpecification,
    JobStatus,
)


class JobRepository(Protocol):
    def submit(
        self,
        specification: JobSpecification,
        *,
        specification_sha256: str,
        created_at: datetime,
    ) -> JobRecord: ...

    def get_job(self, job_id: UUID) -> JobRecord | None: ...

    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        accessible_organization_ids: tuple[UUID, ...] | None,
        limit: int,
        offset: int,
    ) -> Sequence[JobRecord]: ...

    def lease_next(
        self,
        *,
        worker_id: str,
        executor_name: str,
        leased_at: datetime,
        expires_at: datetime,
    ) -> JobLease | None: ...

    def get_lease(self, lease_token: UUID) -> JobLease | None: ...

    def mark_running(self, lease_token: UUID, *, started_at: datetime) -> JobAttemptRecord: ...

    def heartbeat(
        self,
        lease_token: UUID,
        *,
        heartbeat_at: datetime,
        expires_at: datetime,
    ) -> JobLease: ...

    def request_cancel(
        self,
        job_id: UUID,
        *,
        reason: str,
        cancelled_at: datetime,
    ) -> JobRecord: ...

    def is_cancel_requested(self, job_id: UUID) -> bool: ...

    def complete(
        self,
        lease_token: UUID,
        *,
        result: JobResult,
        next_status: JobStatus,
        next_eligible_at: datetime,
        completed_at: datetime,
    ) -> JobRecord: ...

    def expire_leases(self, *, expired_at: datetime) -> Sequence[JobRecord]: ...

    def append_log(self, record: JobLogRecord) -> JobLogRecord: ...

    def list_attempts(self, job_id: UUID) -> Sequence[JobAttemptRecord]: ...

    def list_logs(self, attempt_id: UUID) -> Sequence[JobLogRecord]: ...
