"""Transactional PostgreSQL persistence for the generic job service."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from hiveblot_contracts import (
    JobAttemptRecord,
    JobAttemptStatus,
    JobLease,
    JobLogRecord,
    JobLogStream,
    JobRecord,
    JobResult,
    JobSpecification,
    JobStatus,
)
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from .errors import DuplicateJob, InvalidJobState, JobNotFound, LeaseLost

_RESULT_ADAPTER: TypeAdapter[JobResult] = TypeAdapter(JobResult)
_ACTIVE_STATUSES = {JobStatus.LEASED.value, JobStatus.RUNNING.value}
_TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
    JobStatus.DEAD_LETTER.value,
}


class PostgresJobRepository:
    """Maps durable rows to strict contracts without exposing database entities."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def submit(
        self,
        specification: JobSpecification,
        *,
        specification_sha256: str,
        created_at: datetime,
    ) -> JobRecord:
        payload = Jsonb(specification.model_dump(mode="json"))
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            try:
                row = connection.execute(
                    """
                    INSERT INTO jobs (
                        job_id, job_type, idempotency_key, parent_job_id, trace_id, specification,
                        specification_sha256, visibility, organization_id, submitted_by,
                        status, next_eligible_at, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s
                    )
                    RETURNING *
                    """,
                    (
                        specification.job_id,
                        specification.job_type,
                        specification.idempotency_key,
                        specification.parent_job_id,
                        specification.trace_id,
                        payload,
                        specification_sha256,
                        specification.visibility.value,
                        specification.organization_id,
                        specification.submitted_by,
                        created_at,
                        created_at,
                        created_at,
                    ),
                ).fetchone()
            except psycopg.errors.UniqueViolation:
                connection.rollback()
                row = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE job_id = %s OR (job_type = %s AND idempotency_key = %s)
                    ORDER BY (job_id = %s) DESC
                    LIMIT 1
                    """,
                    (
                        specification.job_id,
                        specification.job_type,
                        specification.idempotency_key,
                        specification.job_id,
                    ),
                ).fetchone()
                if row is None or row["specification_sha256"] != specification_sha256:
                    raise DuplicateJob(
                        "job ID or idempotency key is already associated with different content"
                    ) from None
            assert row is not None
            return _job_from_row(row)

    def get_job(self, job_id: UUID) -> JobRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s",
                (job_id,),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        accessible_organization_ids: tuple[UUID, ...] | None,
        limit: int,
        offset: int,
    ) -> Sequence[JobRecord]:
        clauses: list[str] = []
        parameters: list[object] = []
        if status is not None:
            clauses.append("status = %s")
            parameters.append(status.value)
        if accessible_organization_ids is not None:
            if accessible_organization_ids:
                clauses.append("(visibility = 'public' OR organization_id = ANY(%s::uuid[]))")
                parameters.append(list(accessible_organization_ids))
            else:
                clauses.append("visibility = 'public'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend((limit, offset))
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM jobs {where}
                ORDER BY created_at DESC, job_id DESC
                LIMIT %s OFFSET %s
                """,  # noqa: S608 - the only interpolation is a fixed internal clause
                parameters,
            ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    def lease_next(
        self,
        *,
        worker_id: str,
        executor_name: str,
        leased_at: datetime,
        expires_at: datetime,
    ) -> JobLease | None:
        lease_token = uuid4()
        attempt_id = uuid4()
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'pending'
                  AND cancel_requested = FALSE
                  AND next_eligible_at <= %s
                ORDER BY next_eligible_at, created_at, job_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (leased_at,),
            ).fetchone()
            if row is None:
                return None
            attempt = int(row["attempt_count"]) + 1
            updated = connection.execute(
                """
                UPDATE jobs
                SET status = 'leased', attempt_count = %s, lease_owner = %s,
                    lease_token = %s, lease_expires_at = %s, updated_at = %s
                WHERE job_id = %s AND status = 'pending'
                RETURNING *
                """,
                (
                    attempt,
                    worker_id,
                    lease_token,
                    expires_at,
                    leased_at,
                    row["job_id"],
                ),
            ).fetchone()
            if updated is None:  # pragma: no cover - row lock makes this defensive
                raise LeaseLost("selected job could not be leased")
            connection.execute(
                """
                INSERT INTO job_attempts (
                    attempt_id, job_id, attempt_number, worker_id, lease_token,
                    status, executor_name, leased_at, lease_expires_at
                ) VALUES (%s, %s, %s, %s, %s, 'leased', %s, %s, %s)
                """,
                (
                    attempt_id,
                    updated["job_id"],
                    attempt,
                    worker_id,
                    lease_token,
                    executor_name,
                    leased_at,
                    expires_at,
                ),
            )
            specification = _specification(updated["specification"])
            return JobLease(
                lease_token=lease_token,
                job_id=updated["job_id"],
                attempt_id=attempt_id,
                attempt=attempt,
                worker_id=worker_id,
                specification=specification,
                leased_at=leased_at,
                expires_at=expires_at,
            )

    def get_lease(self, lease_token: UUID) -> JobLease | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT a.attempt_id, a.job_id, a.attempt_number, a.worker_id,
                       a.leased_at, a.lease_expires_at, j.specification
                FROM job_attempts AS a
                JOIN jobs AS j ON j.job_id = a.job_id
                WHERE a.lease_token = %s
                """,
                (lease_token,),
            ).fetchone()
        return _lease_from_row(row, lease_token) if row is not None else None

    def mark_running(self, lease_token: UUID, *, started_at: datetime) -> JobAttemptRecord:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = _lock_attempt_and_job(connection, lease_token)
            _require_active_lease(row, lease_token, at=started_at)
            if row["attempt_status"] == JobAttemptStatus.LEASED.value:
                connection.execute(
                    """
                    UPDATE job_attempts
                    SET status = 'running', started_at = %s
                    WHERE attempt_id = %s
                    """,
                    (started_at, row["attempt_id"]),
                )
                connection.execute(
                    """
                    UPDATE jobs SET status = 'running', updated_at = %s
                    WHERE job_id = %s
                    """,
                    (started_at, row["job_id"]),
                )
            elif row["attempt_status"] != JobAttemptStatus.RUNNING.value:
                raise LeaseLost("job attempt is already terminal")
            attempt = connection.execute(
                "SELECT * FROM job_attempts WHERE attempt_id = %s",
                (row["attempt_id"],),
            ).fetchone()
            assert attempt is not None
            return _attempt_from_row(attempt)

    def heartbeat(
        self,
        lease_token: UUID,
        *,
        heartbeat_at: datetime,
        expires_at: datetime,
    ) -> JobLease:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = _lock_attempt_and_job(connection, lease_token)
            _require_active_lease(row, lease_token, at=heartbeat_at)
            connection.execute(
                """
                UPDATE job_attempts SET lease_expires_at = %s WHERE attempt_id = %s
                """,
                (expires_at, row["attempt_id"]),
            )
            connection.execute(
                """
                UPDATE jobs SET lease_expires_at = %s, updated_at = %s WHERE job_id = %s
                """,
                (expires_at, heartbeat_at, row["job_id"]),
            )
            row["lease_expires_at"] = expires_at
            return _lease_from_joined_row(row, lease_token)

    def request_cancel(
        self,
        job_id: UUID,
        *,
        reason: str,
        cancelled_at: datetime,
    ) -> JobRecord:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFound(f"job {job_id} does not exist")
            current = JobStatus(row["status"])
            if current is JobStatus.CANCELLED:
                return _job_from_row(row)
            if current.value in _TERMINAL_STATUSES:
                raise InvalidJobState(f"cannot cancel a {current.value} job")
            if current is JobStatus.PENDING:
                row = connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'cancelled', cancel_requested = TRUE,
                        cancellation_reason = %s, completed_at = %s, updated_at = %s
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (reason, cancelled_at, cancelled_at, job_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    UPDATE jobs
                    SET cancel_requested = TRUE, cancellation_reason = %s, updated_at = %s
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (reason, cancelled_at, job_id),
                ).fetchone()
            assert row is not None
            return _job_from_row(row)

    def is_cancel_requested(self, job_id: UUID) -> bool:
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM jobs WHERE job_id = %s",
                (job_id,),
            ).fetchone()
        if row is None:
            raise JobNotFound(f"job {job_id} does not exist")
        return bool(row[0])

    def complete(
        self,
        lease_token: UUID,
        *,
        result: JobResult,
        next_status: JobStatus,
        next_eligible_at: datetime,
        completed_at: datetime,
    ) -> JobRecord:
        payload = result.model_dump(mode="json")
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = _lock_attempt_and_job(connection, lease_token)
            if row["attempt_status"] in {
                JobAttemptStatus.SUCCEEDED.value,
                JobAttemptStatus.FAILED.value,
                JobAttemptStatus.CANCELLED.value,
            }:
                if row["attempt_result"] != payload:
                    raise InvalidJobState("attempt already has a different terminal result")
                job = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = %s",
                    (row["job_id"],),
                ).fetchone()
                assert job is not None
                return _job_from_row(job)
            _require_active_lease(row, lease_token, at=completed_at)
            if row["cancel_requested"] and result.status != "cancelled":
                raise InvalidJobState("a cancelled job requires a cancelled result")
            if not row["cancel_requested"] and result.status == "cancelled":
                raise InvalidJobState(
                    "a job without a cancellation request cannot publish cancellation"
                )

            attempt_status = JobAttemptStatus(result.status)
            connection.execute(
                """
                UPDATE job_attempts
                SET status = %s, completed_at = %s, result = %s
                WHERE attempt_id = %s
                """,
                (
                    attempt_status.value,
                    completed_at,
                    Jsonb(payload),
                    row["attempt_id"],
                ),
            )

            terminal = next_status.value in _TERMINAL_STATUSES
            stored_result = Jsonb(payload) if terminal else None
            job = connection.execute(
                """
                UPDATE jobs
                SET status = %s, next_eligible_at = %s,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    result = %s, updated_at = %s, completed_at = %s
                WHERE job_id = %s
                RETURNING *
                """,
                (
                    next_status.value,
                    next_eligible_at,
                    stored_result,
                    completed_at,
                    completed_at if terminal else None,
                    row["job_id"],
                ),
            ).fetchone()
            assert job is not None
            if next_status is JobStatus.SUCCEEDED:
                for output in result.outputs:
                    existing = connection.execute(
                        """
                        SELECT attempt_id, artifact_id FROM job_outputs
                        WHERE job_id = %s AND output_name = %s
                        """,
                        (row["job_id"], output.name),
                    ).fetchone()
                    if existing is not None:
                        if (
                            existing["attempt_id"] != row["attempt_id"]
                            or existing["artifact_id"] != output.artifact.artifact_id
                        ):
                            raise InvalidJobState(
                                "canonical job output already references different content"
                            )
                        continue
                    connection.execute(
                        """
                        INSERT INTO job_outputs (
                            job_id, output_name, attempt_id, artifact_id, created_at
                        ) VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            row["job_id"],
                            output.name,
                            row["attempt_id"],
                            output.artifact.artifact_id,
                            completed_at,
                        ),
                    )
            return _job_from_row(job)

    def expire_leases(self, *, expired_at: datetime) -> Sequence[JobRecord]:
        records: list[JobRecord] = []
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status IN ('leased', 'running') AND lease_expires_at <= %s
                ORDER BY lease_expires_at, job_id
                FOR UPDATE SKIP LOCKED
                """,
                (expired_at,),
            ).fetchall()
            for row in rows:
                specification = _specification(row["specification"])
                if row["cancel_requested"]:
                    next_status = JobStatus.CANCELLED
                elif int(row["attempt_count"]) >= specification.max_attempts:
                    next_status = JobStatus.DEAD_LETTER
                else:
                    next_status = JobStatus.PENDING
                terminal = next_status in {JobStatus.CANCELLED, JobStatus.DEAD_LETTER}
                connection.execute(
                    """
                    UPDATE job_attempts
                    SET status = 'lease_expired', completed_at = %s
                    WHERE lease_token = %s AND status IN ('leased', 'running')
                    """,
                    (expired_at, row["lease_token"]),
                )
                updated = connection.execute(
                    """
                    UPDATE jobs
                    SET status = %s, lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL, next_eligible_at = %s,
                        updated_at = %s, completed_at = %s
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (
                        next_status.value,
                        expired_at,
                        expired_at,
                        expired_at if terminal else None,
                        row["job_id"],
                    ),
                ).fetchone()
                assert updated is not None
                records.append(_job_from_row(updated))
        return tuple(records)

    def append_log(self, record: JobLogRecord) -> JobLogRecord:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM job_attempts WHERE attempt_id = %s",
                    (record.attempt_id,),
                ).fetchone()
                is None
            ):
                raise JobNotFound(f"job attempt {record.attempt_id} does not exist")
            try:
                row = connection.execute(
                    """
                    INSERT INTO job_logs (
                        log_id, attempt_id, sequence, stream, content, truncated, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        record.log_id,
                        record.attempt_id,
                        record.sequence,
                        record.stream.value,
                        record.content,
                        record.truncated,
                        record.created_at,
                    ),
                ).fetchone()
            except psycopg.errors.UniqueViolation:
                connection.rollback()
                row = connection.execute(
                    """
                    SELECT * FROM job_logs
                    WHERE attempt_id = %s AND stream = %s AND sequence = %s
                    """,
                    (record.attempt_id, record.stream.value, record.sequence),
                ).fetchone()
                if (
                    row is None
                    or _log_from_row(row).model_copy(update={"log_id": record.log_id}) != record
                ):
                    raise InvalidJobState(
                        "job log sequence already contains different content"
                    ) from None
                return _log_from_row(row)
            assert row is not None
            return _log_from_row(row)

    def list_attempts(self, job_id: UUID) -> Sequence[JobAttemptRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT * FROM job_attempts WHERE job_id = %s
                ORDER BY attempt_number, attempt_id
                """,
                (job_id,),
            ).fetchall()
        return tuple(_attempt_from_row(row) for row in rows)

    def list_logs(self, attempt_id: UUID) -> Sequence[JobLogRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            exists = connection.execute(
                "SELECT 1 FROM job_attempts WHERE attempt_id = %s",
                (attempt_id,),
            ).fetchone()
            if exists is None:
                raise JobNotFound(f"job attempt {attempt_id} does not exist")
            rows = connection.execute(
                """
                SELECT * FROM job_logs WHERE attempt_id = %s
                ORDER BY sequence, stream, log_id
                """,
                (attempt_id,),
            ).fetchall()
        return tuple(_log_from_row(row) for row in rows)


def _lock_attempt_and_job(
    connection: Connection[Mapping[str, Any]],
    lease_token: UUID,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT a.attempt_id, a.job_id, a.attempt_number, a.worker_id,
               a.status AS attempt_status, a.leased_at, a.started_at,
               a.completed_at AS attempt_completed_at,
               a.lease_expires_at, a.result AS attempt_result,
               j.status AS job_status, j.cancel_requested,
               j.lease_token AS active_lease_token, j.specification
        FROM job_attempts AS a
        JOIN jobs AS j ON j.job_id = a.job_id
        WHERE a.lease_token = %s
        FOR UPDATE OF a, j
        """,
        (lease_token,),
    ).fetchone()
    if row is None:
        raise JobNotFound(f"job lease {lease_token} does not exist")
    return dict(row)


def _require_active_lease(row: Mapping[str, Any], lease_token: UUID, *, at: datetime) -> None:
    if row["job_status"] not in _ACTIVE_STATUSES:
        raise LeaseLost("job no longer has an active lease")
    if row["active_lease_token"] != lease_token:
        raise LeaseLost("job lease belongs to another worker attempt")
    if row["lease_expires_at"] <= at:
        raise LeaseLost("job lease has expired")


def _job_from_row(row: Mapping[str, Any]) -> JobRecord:
    result_payload = row.get("result")
    return JobRecord(
        job_id=row["job_id"],
        specification=_specification(row["specification"]),
        status=JobStatus(row["status"]),
        attempt_count=row["attempt_count"],
        cancel_requested=row["cancel_requested"],
        cancellation_reason=row.get("cancellation_reason"),
        current_worker_id=row.get("lease_owner"),
        lease_expires_at=row.get("lease_expires_at"),
        result=(
            _RESULT_ADAPTER.validate_json(json.dumps(result_payload), strict=True)
            if result_payload is not None
            else None
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row.get("completed_at"),
    )


def _attempt_from_row(row: Mapping[str, Any]) -> JobAttemptRecord:
    result_payload = row.get("result")
    return JobAttemptRecord(
        attempt_id=row["attempt_id"],
        job_id=row["job_id"],
        attempt=row["attempt_number"],
        worker_id=row["worker_id"],
        status=JobAttemptStatus(row["status"]),
        executor_name=row["executor_name"],
        leased_at=row["leased_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        lease_expires_at=row["lease_expires_at"],
        result=(
            _RESULT_ADAPTER.validate_json(json.dumps(result_payload), strict=True)
            if result_payload is not None
            else None
        ),
    )


def _lease_from_row(row: Mapping[str, Any], lease_token: UUID) -> JobLease:
    return JobLease(
        lease_token=lease_token,
        job_id=row["job_id"],
        attempt_id=row["attempt_id"],
        attempt=row["attempt_number"],
        worker_id=row["worker_id"],
        specification=_specification(row["specification"]),
        leased_at=row["leased_at"],
        expires_at=row["lease_expires_at"],
    )


def _lease_from_joined_row(row: Mapping[str, Any], lease_token: UUID) -> JobLease:
    return _lease_from_row(row, lease_token)


def _log_from_row(row: Mapping[str, Any]) -> JobLogRecord:
    return JobLogRecord(
        log_id=row["log_id"],
        attempt_id=row["attempt_id"],
        sequence=row["sequence"],
        stream=JobLogStream(row["stream"]),
        content=row["content"],
        truncated=row["truncated"],
        created_at=row["created_at"],
    )


def _specification(value: object) -> JobSpecification:
    return JobSpecification.model_validate_json(json.dumps(value), strict=True)
