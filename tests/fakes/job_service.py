"""Deterministic in-memory persistence for generic job-service tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from hiveblot_contracts import (
    JobAttemptRecord,
    JobAttemptStatus,
    JobLease,
    JobLogRecord,
    JobRecord,
    JobResult,
    JobSpecification,
    JobStatus,
)
from hiveblot_job_service import DuplicateJob, InvalidJobState, JobNotFound, LeaseLost


class InMemoryJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[UUID, JobRecord] = {}
        self.specification_hashes: dict[UUID, str] = {}
        self.idempotency: dict[tuple[str, str], UUID] = {}
        self.next_eligible: dict[UUID, datetime] = {}
        self.leases: dict[UUID, JobLease] = {}
        self.attempts: dict[UUID, JobAttemptRecord] = {}
        self.logs: dict[UUID, list[JobLogRecord]] = {}
        self.outputs: dict[tuple[UUID, str], tuple[UUID, UUID]] = {}

    def submit(
        self,
        specification: JobSpecification,
        *,
        specification_sha256: str,
        created_at: datetime,
    ) -> JobRecord:
        key = (specification.job_type, specification.idempotency_key)
        existing_id = (
            specification.job_id if specification.job_id in self.jobs else self.idempotency.get(key)
        )
        if existing_id is not None:
            if self.specification_hashes[existing_id] != specification_sha256:
                raise DuplicateJob("job identity already has different content")
            return self.jobs[existing_id]
        record = JobRecord(
            job_id=specification.job_id,
            specification=specification,
            status=JobStatus.PENDING,
            attempt_count=0,
            cancel_requested=False,
            created_at=created_at,
            updated_at=created_at,
        )
        self.jobs[record.job_id] = record
        self.specification_hashes[record.job_id] = specification_sha256
        self.idempotency[key] = record.job_id
        self.next_eligible[record.job_id] = created_at
        return record

    def get_job(self, job_id: UUID) -> JobRecord | None:
        return self.jobs.get(job_id)

    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        limit: int,
        offset: int,
    ) -> Sequence[JobRecord]:
        records = sorted(
            self.jobs.values(),
            key=lambda item: (item.created_at, item.job_id),
            reverse=True,
        )
        if status is not None:
            records = [item for item in records if item.status is status]
        return tuple(records[offset : offset + limit])

    def lease_next(
        self,
        *,
        worker_id: str,
        executor_name: str,
        leased_at: datetime,
        expires_at: datetime,
    ) -> JobLease | None:
        eligible = sorted(
            (
                item
                for item in self.jobs.values()
                if item.status is JobStatus.PENDING
                and not item.cancel_requested
                and self.next_eligible[item.job_id] <= leased_at
            ),
            key=lambda item: (self.next_eligible[item.job_id], item.created_at, item.job_id),
        )
        if not eligible:
            return None
        current = eligible[0]
        attempt_number = current.attempt_count + 1
        lease = JobLease(
            lease_token=uuid4(),
            job_id=current.job_id,
            attempt_id=uuid4(),
            attempt=attempt_number,
            worker_id=worker_id,
            specification=current.specification,
            leased_at=leased_at,
            expires_at=expires_at,
        )
        self.leases[lease.lease_token] = lease
        self.attempts[lease.attempt_id] = JobAttemptRecord(
            attempt_id=lease.attempt_id,
            job_id=lease.job_id,
            attempt=lease.attempt,
            worker_id=worker_id,
            status=JobAttemptStatus.LEASED,
            executor_name=executor_name,
            leased_at=leased_at,
            lease_expires_at=expires_at,
        )
        self.jobs[current.job_id] = current.model_copy(
            update={
                "status": JobStatus.LEASED,
                "attempt_count": attempt_number,
                "current_worker_id": worker_id,
                "lease_expires_at": expires_at,
                "updated_at": leased_at,
            }
        )
        return lease

    def get_lease(self, lease_token: UUID) -> JobLease | None:
        return self.leases.get(lease_token)

    def mark_running(self, lease_token: UUID, *, started_at: datetime) -> JobAttemptRecord:
        lease, job, attempt = self._active(lease_token, started_at)
        if attempt.status not in {JobAttemptStatus.LEASED, JobAttemptStatus.RUNNING}:
            raise LeaseLost("attempt is terminal")
        if attempt.status is JobAttemptStatus.LEASED:
            attempt = attempt.model_copy(
                update={"status": JobAttemptStatus.RUNNING, "started_at": started_at}
            )
            self.attempts[attempt.attempt_id] = attempt
            self.jobs[job.job_id] = job.model_copy(
                update={"status": JobStatus.RUNNING, "updated_at": started_at}
            )
        del lease
        return attempt

    def heartbeat(
        self,
        lease_token: UUID,
        *,
        heartbeat_at: datetime,
        expires_at: datetime,
    ) -> JobLease:
        lease, job, attempt = self._active(lease_token, heartbeat_at)
        updated_lease = lease.model_copy(update={"expires_at": expires_at})
        self.leases[lease_token] = updated_lease
        self.attempts[attempt.attempt_id] = attempt.model_copy(
            update={"lease_expires_at": expires_at}
        )
        self.jobs[job.job_id] = job.model_copy(
            update={"lease_expires_at": expires_at, "updated_at": heartbeat_at}
        )
        return updated_lease

    def request_cancel(
        self,
        job_id: UUID,
        *,
        reason: str,
        cancelled_at: datetime,
    ) -> JobRecord:
        try:
            job = self.jobs[job_id]
        except KeyError as exc:
            raise JobNotFound(str(job_id)) from exc
        if job.status is JobStatus.CANCELLED:
            return job
        if job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.DEAD_LETTER,
        }:
            raise InvalidJobState(f"cannot cancel a {job.status.value} job")
        updates: dict[str, object] = {
            "cancel_requested": True,
            "cancellation_reason": reason,
            "updated_at": cancelled_at,
        }
        if job.status is JobStatus.PENDING:
            updates.update(
                {
                    "status": JobStatus.CANCELLED,
                    "completed_at": cancelled_at,
                }
            )
        job = job.model_copy(update=updates)
        self.jobs[job_id] = job
        return job

    def is_cancel_requested(self, job_id: UUID) -> bool:
        try:
            return self.jobs[job_id].cancel_requested
        except KeyError as exc:
            raise JobNotFound(str(job_id)) from exc

    def complete(
        self,
        lease_token: UUID,
        *,
        result: JobResult,
        next_status: JobStatus,
        next_eligible_at: datetime,
        completed_at: datetime,
    ) -> JobRecord:
        lease = self.leases.get(lease_token)
        if lease is None:
            raise JobNotFound(str(lease_token))
        attempt = self.attempts[lease.attempt_id]
        if attempt.status in {
            JobAttemptStatus.SUCCEEDED,
            JobAttemptStatus.FAILED,
            JobAttemptStatus.CANCELLED,
        }:
            if attempt.result != result:
                raise InvalidJobState("attempt already contains another result")
            return self.jobs[lease.job_id]
        _, job, _ = self._active(lease_token, completed_at)
        if job.cancel_requested and result.status != "cancelled":
            raise InvalidJobState("a cancelled job requires a cancelled result")
        if not job.cancel_requested and result.status == "cancelled":
            raise InvalidJobState(
                "a job without a cancellation request cannot publish cancellation"
            )
        attempt_status = JobAttemptStatus(result.status)
        self.attempts[attempt.attempt_id] = attempt.model_copy(
            update={
                "status": attempt_status,
                "completed_at": completed_at,
                "result": result,
            }
        )
        terminal = next_status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.DEAD_LETTER,
        }
        job = job.model_copy(
            update={
                "status": next_status,
                "current_worker_id": None,
                "lease_expires_at": None,
                "result": result if terminal else None,
                "updated_at": completed_at,
                "completed_at": completed_at if terminal else None,
            }
        )
        self.jobs[job.job_id] = job
        self.next_eligible[job.job_id] = next_eligible_at
        if next_status is JobStatus.SUCCEEDED:
            for output in result.outputs:
                key = (job.job_id, output.name)
                value = (attempt.attempt_id, output.artifact.artifact_id)
                if key in self.outputs and self.outputs[key] != value:
                    raise InvalidJobState("canonical output conflicts")
                self.outputs[key] = value
        return job

    def expire_leases(self, *, expired_at: datetime) -> Sequence[JobRecord]:
        expired: list[JobRecord] = []
        for job in tuple(self.jobs.values()):
            if (
                job.status not in {JobStatus.LEASED, JobStatus.RUNNING}
                or job.lease_expires_at is None
                or job.lease_expires_at > expired_at
            ):
                continue
            lease = next(
                item
                for item in self.leases.values()
                if item.job_id == job.job_id and item.attempt == job.attempt_count
            )
            attempt = self.attempts[lease.attempt_id]
            self.attempts[attempt.attempt_id] = attempt.model_copy(
                update={
                    "status": JobAttemptStatus.LEASE_EXPIRED,
                    "completed_at": expired_at,
                }
            )
            if job.cancel_requested:
                status = JobStatus.CANCELLED
            elif job.attempt_count >= job.specification.max_attempts:
                status = JobStatus.DEAD_LETTER
            else:
                status = JobStatus.PENDING
            terminal = status in {JobStatus.CANCELLED, JobStatus.DEAD_LETTER}
            job = job.model_copy(
                update={
                    "status": status,
                    "current_worker_id": None,
                    "lease_expires_at": None,
                    "updated_at": expired_at,
                    "completed_at": expired_at if terminal else None,
                }
            )
            self.jobs[job.job_id] = job
            self.next_eligible[job.job_id] = expired_at
            expired.append(job)
        return tuple(expired)

    def append_log(self, record: JobLogRecord) -> JobLogRecord:
        if record.attempt_id not in self.attempts:
            raise JobNotFound(str(record.attempt_id))
        records = self.logs.setdefault(record.attempt_id, [])
        existing = next(
            (
                item
                for item in records
                if item.stream is record.stream and item.sequence == record.sequence
            ),
            None,
        )
        if existing is not None:
            if existing.model_copy(update={"log_id": record.log_id}) != record:
                raise InvalidJobState("log sequence conflicts")
            return existing
        records.append(record)
        return record

    def list_attempts(self, job_id: UUID) -> Sequence[JobAttemptRecord]:
        return tuple(
            sorted(
                (item for item in self.attempts.values() if item.job_id == job_id),
                key=lambda item: (item.attempt, item.attempt_id),
            )
        )

    def list_logs(self, attempt_id: UUID) -> Sequence[JobLogRecord]:
        if attempt_id not in self.attempts:
            raise JobNotFound(str(attempt_id))
        return tuple(
            sorted(
                self.logs.get(attempt_id, []),
                key=lambda item: (item.sequence, item.stream.value, item.log_id),
            )
        )

    def _active(
        self,
        lease_token: UUID,
        at: datetime,
    ) -> tuple[JobLease, JobRecord, JobAttemptRecord]:
        lease = self.leases.get(lease_token)
        if lease is None:
            raise JobNotFound(str(lease_token))
        job = self.jobs[lease.job_id]
        attempt = self.attempts[lease.attempt_id]
        if job.status not in {JobStatus.LEASED, JobStatus.RUNNING}:
            raise LeaseLost("job no longer has an active lease")
        if job.attempt_count != lease.attempt or job.lease_expires_at is None:
            raise LeaseLost("lease was superseded")
        if job.lease_expires_at <= at:
            raise LeaseLost("lease expired")
        return lease, job, attempt
