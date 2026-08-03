"""Transactional PostgreSQL queue, rate limits, and fetch-attempt provenance."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from hiveblot_contracts import (
    ArtifactReference,
    CrawlFetchAttemptOutcome,
    CrawlFetchAttemptRecord,
    CrawlFetchError,
    CrawlFetchLease,
    CrawlFetchTaskRecord,
    CrawlFetchTaskStatus,
    CrawlMetricsSnapshot,
    DiscoveryAcquisitionMethod,
    DomainRequestPermit,
)
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from .errors import FetchLeaseLost, FetchTaskNotFound, FetchValidationError
from .http_fetcher import RobotsCacheEntry

_ERROR_ADAPTER = TypeAdapter(CrawlFetchError)

FETCH_TASK_SELECT = """
SELECT task.*,
    result_blob.sha256 AS result_sha256,
    result_blob.media_type AS result_media_type,
    result_blob.byte_size AS result_byte_size,
    attempt.attempt_number AS current_attempt_number,
    attempt.leased_at AS current_leased_at
FROM crawl_fetch_tasks AS task
LEFT JOIN artifacts AS result_artifact ON result_artifact.artifact_id = task.result_artifact_id
LEFT JOIN artifact_blobs AS result_blob ON result_blob.sha256 = result_artifact.blob_sha256
LEFT JOIN crawl_fetch_attempts AS attempt ON attempt.attempt_id = task.current_attempt_id
"""

FETCH_ATTEMPT_SELECT = """
SELECT attempt.*,
    blob.sha256 AS artifact_sha256,
    blob.media_type AS artifact_media_type,
    blob.byte_size AS artifact_byte_size
FROM crawl_fetch_attempts AS attempt
LEFT JOIN artifacts AS artifact ON artifact.artifact_id = attempt.artifact_id
LEFT JOIN artifact_blobs AS blob ON blob.sha256 = artifact.blob_sha256
"""


class PostgresFetchRepository:
    def __init__(
        self,
        database_url: str,
        *,
        default_minimum_interval_milliseconds: int = 1000,
        default_maximum_concurrency: int = 2,
    ) -> None:
        self._database_url = database_url
        self._default_minimum_interval_milliseconds = default_minimum_interval_milliseconds
        self._default_maximum_concurrency = default_maximum_concurrency

    def enqueue_eligible(
        self,
        *,
        enqueued_at: datetime,
        limit: int,
        max_attempts: int,
        minimum_interval_milliseconds: int,
        maximum_concurrency: int,
    ) -> int:
        created = 0
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT frontier.*
                FROM crawl_frontier AS frontier
                WHERE frontier.status = 'pending'
                  AND frontier.access_status = 'allowed'
                  AND frontier.next_eligible_fetch_at <= %s
                  AND cardinality(frontier.expected_media_types) > 0
                  AND NOT EXISTS (
                      SELECT 1 FROM crawl_fetch_tasks AS active
                      WHERE active.frontier_id = frontier.frontier_id
                        AND active.status IN ('pending', 'leased', 'retry_wait')
                  )
                ORDER BY frontier.priority DESC, frontier.next_eligible_fetch_at,
                    frontier.frontier_id
                FOR UPDATE OF frontier SKIP LOCKED
                LIMIT %s
                """,
                (enqueued_at, limit),
            ).fetchall()
            for row in rows:
                domain = _domain(row["canonical_url"])
                connection.execute(
                    """
                    INSERT INTO crawl_domain_policies (
                        domain, minimum_interval_milliseconds, maximum_concurrency,
                        next_request_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (domain) DO NOTHING
                    """,
                    (
                        domain,
                        minimum_interval_milliseconds,
                        maximum_concurrency,
                        enqueued_at,
                        enqueued_at,
                    ),
                )
                result = connection.execute(
                    """
                    INSERT INTO crawl_fetch_tasks (
                        task_id, frontier_id, canonical_url, domain, acquisition_method,
                        expected_media_types, status, priority, max_attempts,
                        next_eligible_at, trace_id, etag, last_modified_header,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (frontier_id)
                        WHERE status IN ('pending', 'leased', 'retry_wait')
                        DO NOTHING
                    RETURNING task_id
                    """,
                    (
                        uuid4(),
                        row["frontier_id"],
                        row["canonical_url"],
                        domain,
                        row["acquisition_method"],
                        row["expected_media_types"],
                        row["priority"],
                        max_attempts,
                        row["next_eligible_fetch_at"],
                        row["trace_id"],
                        row["etag"],
                        row["last_modified_header"],
                        enqueued_at,
                        enqueued_at,
                    ),
                ).fetchone()
                created += int(result is not None)
        return created

    def lease_next(
        self,
        *,
        worker_id: str,
        leased_at: datetime,
        expires_at: datetime,
    ) -> CrawlFetchLease | None:
        lease_token = uuid4()
        attempt_id = uuid4()
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT task.*
                FROM crawl_fetch_tasks AS task
                JOIN crawl_frontier AS frontier ON frontier.frontier_id = task.frontier_id
                WHERE task.status IN ('pending', 'retry_wait')
                  AND task.next_eligible_at <= %s
                  AND frontier.status IN ('pending', 'retry_wait')
                ORDER BY task.priority DESC, task.next_eligible_at, task.created_at, task.task_id
                FOR UPDATE OF task, frontier SKIP LOCKED
                LIMIT 1
                """,
                (leased_at,),
            ).fetchone()
            if row is None:
                return None
            attempt = int(row["attempt_count"]) + 1
            connection.execute(
                """
                UPDATE crawl_fetch_tasks
                SET status = 'leased', attempt_count = %s, lease_owner = %s,
                    lease_token = %s, lease_expires_at = %s, current_attempt_id = %s,
                    updated_at = %s
                WHERE task_id = %s
                """,
                (
                    attempt,
                    worker_id,
                    lease_token,
                    expires_at,
                    attempt_id,
                    leased_at,
                    row["task_id"],
                ),
            )
            connection.execute(
                """
                INSERT INTO crawl_fetch_attempts (
                    attempt_id, task_id, frontier_id, attempt_number, worker_id,
                    lease_token, trace_id, outcome, request_url, leased_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'leased', %s, %s)
                """,
                (
                    attempt_id,
                    row["task_id"],
                    row["frontier_id"],
                    attempt,
                    worker_id,
                    lease_token,
                    row["trace_id"],
                    row["canonical_url"],
                    leased_at,
                ),
            )
            connection.execute(
                """
                UPDATE crawl_frontier
                SET status = 'leased', attempt_count = %s, lease_owner = %s,
                    lease_token = %s, lease_expires_at = %s,
                    version = version + 1, updated_at = %s
                WHERE frontier_id = %s
                """,
                (
                    attempt,
                    worker_id,
                    lease_token,
                    expires_at,
                    leased_at,
                    row["frontier_id"],
                ),
            )
            return _lease_from_values(
                row,
                worker_id=worker_id,
                lease_token=lease_token,
                attempt_id=attempt_id,
                attempt=attempt,
                leased_at=leased_at,
                expires_at=expires_at,
            )

    def heartbeat(
        self,
        lease_token: UUID,
        *,
        heartbeat_at: datetime,
        expires_at: datetime,
    ) -> CrawlFetchLease:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = self._lock_active_task(connection, lease_token, at=heartbeat_at)
            connection.execute(
                """
                UPDATE crawl_fetch_tasks
                SET lease_expires_at = %s, updated_at = %s
                WHERE task_id = %s
                """,
                (expires_at, heartbeat_at, row["task_id"]),
            )
            connection.execute(
                """
                UPDATE crawl_frontier
                SET lease_expires_at = %s, updated_at = %s
                WHERE frontier_id = %s AND lease_token = %s
                """,
                (expires_at, heartbeat_at, row["frontier_id"], lease_token),
            )
            return _lease_from_values(
                row,
                worker_id=row["lease_owner"],
                lease_token=lease_token,
                attempt_id=row["current_attempt_id"],
                attempt=row["attempt_count"],
                leased_at=row["attempt_leased_at"],
                expires_at=expires_at,
            )

    def reap_expired_leases(self, *, expired_at: datetime) -> Sequence[CrawlFetchTaskRecord]:
        records: list[CrawlFetchTaskRecord] = []
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT * FROM crawl_fetch_tasks
                WHERE status = 'leased' AND lease_expires_at <= %s
                ORDER BY lease_expires_at, task_id
                FOR UPDATE SKIP LOCKED
                """,
                (expired_at,),
            ).fetchall()
            for row in rows:
                terminal = int(row["attempt_count"]) >= int(row["max_attempts"])
                task_status = "dead_letter" if terminal else "retry_wait"
                frontier_status = "dead_letter" if terminal else "retry_wait"
                error = CrawlFetchError(
                    code="lease_expired",
                    message="fetch worker lease expired before result publication",
                    retryable=not terminal,
                )
                connection.execute(
                    """
                    UPDATE crawl_fetch_attempts
                    SET outcome = 'lease_expired', error_json = %s, completed_at = %s
                    WHERE attempt_id = %s AND outcome = 'leased'
                    """,
                    (
                        Jsonb(error.model_dump(mode="json")),
                        expired_at,
                        row["current_attempt_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE crawl_fetch_tasks
                    SET status = %s, next_eligible_at = %s,
                        lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                        current_attempt_id = NULL, last_error = %s,
                        updated_at = %s, completed_at = %s
                    WHERE task_id = %s
                    """,
                    (
                        task_status,
                        expired_at,
                        Jsonb(error.model_dump(mode="json")),
                        expired_at,
                        expired_at if terminal else None,
                        row["task_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE crawl_frontier
                    SET status = %s, lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL, next_eligible_fetch_at = %s,
                        version = version + 1, updated_at = %s
                    WHERE frontier_id = %s AND lease_token = %s
                    """,
                    (
                        frontier_status,
                        expired_at,
                        expired_at,
                        row["frontier_id"],
                        row["lease_token"],
                    ),
                )
                records.append(self._get_task(connection, row["task_id"]))
        return tuple(records)

    def complete_success(
        self,
        lease: CrawlFetchLease,
        *,
        artifact: ArtifactReference,
        artifact_deduplicated: bool,
        request_url: str,
        final_url: str,
        http_status: int,
        media_type: str,
        response_sha256: str,
        bytes_downloaded: int,
        latency_milliseconds: int,
        etag: str | None,
        last_modified: str | None,
        started_at: datetime,
        completed_at: datetime,
    ) -> CrawlFetchTaskRecord:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = self._lock_active_task(connection, lease.lease_token, at=completed_at)
            self._require_lease_identity(row, lease)
            self._verify_public_artifact(connection, artifact)
            connection.execute(
                """
                INSERT INTO crawl_frontier_artifacts (
                    frontier_id, artifact_id, trace_id, acquired_at
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (frontier_id, artifact_id) DO NOTHING
                """,
                (lease.frontier_id, artifact.artifact_id, lease.trace_id, completed_at),
            )
            self._finish_attempt(
                connection,
                row,
                outcome=CrawlFetchAttemptOutcome.SUCCEEDED,
                request_url=request_url,
                final_url=final_url,
                http_status=http_status,
                response_media_type=media_type,
                response_sha256=response_sha256,
                bytes_downloaded=bytes_downloaded,
                latency_milliseconds=latency_milliseconds,
                artifact=artifact,
                artifact_deduplicated=artifact_deduplicated,
                retry_after=None,
                error=None,
                started_at=started_at,
                completed_at=completed_at,
            )
            connection.execute(
                """
                UPDATE crawl_fetch_tasks
                SET status = 'succeeded', result_artifact_id = %s,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    current_attempt_id = NULL, etag = %s, last_modified_header = %s,
                    last_error = NULL, updated_at = %s, completed_at = %s
                WHERE task_id = %s
                """,
                (
                    artifact.artifact_id,
                    etag,
                    last_modified,
                    completed_at,
                    completed_at,
                    lease.task_id,
                ),
            )
            connection.execute(
                """
                UPDATE crawl_frontier
                SET status = 'acquired', lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, etag = %s, last_modified_header = %s,
                    last_fetch_at = %s, last_http_status = %s,
                    version = version + 1, updated_at = %s
                WHERE frontier_id = %s AND lease_token = %s
                """,
                (
                    etag,
                    last_modified,
                    completed_at,
                    http_status,
                    completed_at,
                    lease.frontier_id,
                    lease.lease_token,
                ),
            )
            connection.execute(
                """
                INSERT INTO crawl_downstream_tasks (
                    downstream_task_id, fetch_task_id, frontier_id, task_kind,
                    status, artifact_id, trace_id, created_at
                ) VALUES (%s, %s, %s, 'parse', 'pending', %s, %s, %s)
                ON CONFLICT (fetch_task_id, task_kind) DO NOTHING
                """,
                (
                    uuid4(),
                    lease.task_id,
                    lease.frontier_id,
                    artifact.artifact_id,
                    lease.trace_id,
                    completed_at,
                ),
            )
            return self._get_task(connection, lease.task_id)

    def complete_not_modified(
        self,
        lease: CrawlFetchLease,
        *,
        request_url: str,
        final_url: str,
        http_status: int,
        latency_milliseconds: int,
        etag: str | None,
        last_modified: str | None,
        started_at: datetime,
        completed_at: datetime,
    ) -> CrawlFetchTaskRecord:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = self._lock_active_task(connection, lease.lease_token, at=completed_at)
            self._require_lease_identity(row, lease)
            prior = connection.execute(
                "SELECT 1 FROM crawl_frontier_artifacts WHERE frontier_id = %s LIMIT 1",
                (lease.frontier_id,),
            ).fetchone()
            if prior is None:
                raise FetchValidationError("HTTP 304 is invalid before an artifact acquisition")
            self._finish_attempt(
                connection,
                row,
                outcome=CrawlFetchAttemptOutcome.NOT_MODIFIED,
                request_url=request_url,
                final_url=final_url,
                http_status=http_status,
                response_media_type=None,
                response_sha256=None,
                bytes_downloaded=0,
                latency_milliseconds=latency_milliseconds,
                artifact=None,
                artifact_deduplicated=None,
                retry_after=None,
                error=None,
                started_at=started_at,
                completed_at=completed_at,
            )
            connection.execute(
                """
                UPDATE crawl_fetch_tasks
                SET status = 'not_modified', lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, current_attempt_id = NULL,
                    etag = COALESCE(%s, etag),
                    last_modified_header = COALESCE(%s, last_modified_header),
                    last_error = NULL, updated_at = %s, completed_at = %s
                WHERE task_id = %s
                """,
                (etag, last_modified, completed_at, completed_at, lease.task_id),
            )
            connection.execute(
                """
                UPDATE crawl_frontier
                SET status = 'acquired', lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, etag = COALESCE(%s, etag),
                    last_modified_header = COALESCE(%s, last_modified_header),
                    last_fetch_at = %s, last_http_status = %s,
                    version = version + 1, updated_at = %s
                WHERE frontier_id = %s AND lease_token = %s
                """,
                (
                    etag,
                    last_modified,
                    completed_at,
                    http_status,
                    completed_at,
                    lease.frontier_id,
                    lease.lease_token,
                ),
            )
            return self._get_task(connection, lease.task_id)

    def complete_failure(
        self,
        lease: CrawlFetchLease,
        *,
        error: CrawlFetchError,
        request_url: str,
        final_url: str | None,
        http_status: int | None,
        retry_after: datetime | None,
        prohibited: bool,
        started_at: datetime,
        completed_at: datetime,
    ) -> CrawlFetchTaskRecord:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = self._lock_active_task(connection, lease.lease_token, at=completed_at)
            self._require_lease_identity(row, lease)
            retryable = error.retryable and int(row["attempt_count"]) < int(row["max_attempts"])
            if prohibited:
                task_status = CrawlFetchTaskStatus.PROHIBITED
                frontier_status = "prohibited"
                outcome = CrawlFetchAttemptOutcome.PROHIBITED
                completed = completed_at
            elif retryable:
                task_status = CrawlFetchTaskStatus.RETRY_WAIT
                frontier_status = "retry_wait"
                outcome = CrawlFetchAttemptOutcome.RETRYABLE_FAILURE
                completed = None
            else:
                task_status = CrawlFetchTaskStatus.DEAD_LETTER
                frontier_status = "dead_letter"
                outcome = CrawlFetchAttemptOutcome.PERMANENT_FAILURE
                completed = completed_at
            next_eligible = max(
                completed_at,
                retry_after or completed_at + _retry_delay(int(row["attempt_count"])),
            )
            self._finish_attempt(
                connection,
                row,
                outcome=outcome,
                request_url=request_url,
                final_url=final_url,
                http_status=http_status,
                response_media_type=None,
                response_sha256=None,
                bytes_downloaded=None,
                latency_milliseconds=_duration_milliseconds(started_at, completed_at),
                artifact=None,
                artifact_deduplicated=None,
                retry_after=retry_after,
                error=error,
                started_at=started_at,
                completed_at=completed_at,
            )
            connection.execute(
                """
                UPDATE crawl_fetch_tasks
                SET status = %s, next_eligible_at = %s,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    current_attempt_id = NULL, last_error = %s,
                    updated_at = %s, completed_at = %s
                WHERE task_id = %s
                """,
                (
                    task_status.value,
                    next_eligible,
                    Jsonb(error.model_dump(mode="json")),
                    completed_at,
                    completed,
                    lease.task_id,
                ),
            )
            connection.execute(
                """
                UPDATE crawl_frontier
                SET status = %s, lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, next_eligible_fetch_at = %s,
                    access_status = CASE WHEN %s THEN 'prohibited' ELSE access_status END,
                    access_reason = CASE WHEN %s THEN %s ELSE access_reason END,
                    robots_status = CASE WHEN %s THEN 'prohibited' ELSE robots_status END,
                    last_fetch_at = %s, last_http_status = %s,
                    version = version + 1, updated_at = %s
                WHERE frontier_id = %s AND lease_token = %s
                """,
                (
                    frontier_status,
                    next_eligible,
                    prohibited,
                    prohibited,
                    error.code,
                    error.code == "robots_prohibited",
                    completed_at,
                    http_status,
                    completed_at,
                    lease.frontier_id,
                    lease.lease_token,
                ),
            )
            return self._get_task(connection, lease.task_id)

    def get_task(self, task_id: UUID) -> CrawlFetchTaskRecord | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                FETCH_TASK_SELECT + " WHERE task.task_id = %s",
                (task_id,),
            ).fetchone()
        return _task_from_row(row) if row is not None else None

    def list_attempts(self, task_id: UUID) -> Sequence[CrawlFetchAttemptRecord]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                FETCH_ATTEMPT_SELECT
                + " WHERE attempt.task_id = %s ORDER BY attempt.attempt_number, attempt.attempt_id",
                (task_id,),
            ).fetchall()
        return tuple(_attempt_from_row(row) for row in rows)

    def metrics(self, *, measured_at: datetime) -> CrawlMetricsSnapshot:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            queue = connection.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('pending', 'retry_wait')) AS queue_depth,
                    COUNT(*) FILTER (WHERE status = 'leased') AS leased_tasks,
                    COUNT(DISTINCT lease_owner) FILTER (WHERE status = 'leased') AS active_workers,
                    COUNT(*) FILTER (WHERE status = 'retry_wait') AS retry_count,
                    COUNT(*) FILTER (WHERE status = 'dead_letter') AS dead_letter_count
                FROM crawl_fetch_tasks
                """
            ).fetchone()
            totals = connection.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE outcome IN ('succeeded', 'not_modified'))
                        AS completed_fetches,
                    COALESCE(SUM(bytes_downloaded), 0) AS bytes_downloaded,
                    COUNT(*) FILTER (WHERE artifact_deduplicated = TRUE)
                        AS deduplicated_artifacts,
                    COALESCE(SUM(latency_milliseconds), 0)
                        AS fetch_latency_milliseconds_sum
                FROM crawl_fetch_attempts
                """
            ).fetchone()
            statuses = connection.execute(
                """
                SELECT http_status::TEXT AS key, COUNT(*) AS count
                FROM crawl_fetch_attempts WHERE http_status IS NOT NULL
                GROUP BY http_status ORDER BY http_status
                """
            ).fetchall()
            errors = connection.execute(
                """
                SELECT error_json->>'code' AS key, COUNT(*) AS count
                FROM crawl_fetch_attempts WHERE error_json IS NOT NULL
                GROUP BY error_json->>'code' ORDER BY error_json->>'code'
                """
            ).fetchall()
        assert queue is not None and totals is not None
        return CrawlMetricsSnapshot(
            measured_at=measured_at,
            queue_depth=int(queue["queue_depth"]),
            leased_tasks=int(queue["leased_tasks"]),
            active_workers=int(queue["active_workers"]),
            retry_count=int(queue["retry_count"]),
            dead_letter_count=int(queue["dead_letter_count"]),
            completed_fetches=int(totals["completed_fetches"]),
            bytes_downloaded=int(totals["bytes_downloaded"]),
            deduplicated_artifacts=int(totals["deduplicated_artifacts"]),
            fetch_latency_milliseconds_sum=int(totals["fetch_latency_milliseconds_sum"]),
            http_status_counts={row["key"]: int(row["count"]) for row in statuses},
            source_error_counts={row["key"]: int(row["count"]) for row in errors},
        )

    def try_acquire_domain_permit(
        self,
        *,
        domain: str,
        worker_id: str,
        acquired_at: datetime,
        expires_at: datetime,
    ) -> tuple[DomainRequestPermit | None, datetime]:
        permit_id = uuid4()
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute(
                """
                INSERT INTO crawl_domain_policies (
                    domain, minimum_interval_milliseconds, maximum_concurrency,
                    next_request_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (domain) DO NOTHING
                """,
                (
                    domain,
                    self._default_minimum_interval_milliseconds,
                    self._default_maximum_concurrency,
                    acquired_at,
                    acquired_at,
                ),
            )
            policy = connection.execute(
                "SELECT * FROM crawl_domain_policies WHERE domain = %s FOR UPDATE",
                (domain,),
            ).fetchone()
            assert policy is not None
            connection.execute(
                "DELETE FROM crawl_domain_permits WHERE domain = %s AND expires_at <= %s",
                (domain, acquired_at),
            )
            active = connection.execute(
                """
                SELECT COUNT(*) AS count, MIN(expires_at) AS first_expiration
                FROM crawl_domain_permits WHERE domain = %s
                """,
                (domain,),
            ).fetchone()
            assert active is not None
            rate_ready_at = max(acquired_at, policy["next_request_at"])
            concurrency_ready_at = active["first_expiration"] or acquired_at
            if (
                int(active["count"]) >= int(policy["maximum_concurrency"])
                or policy["next_request_at"] > acquired_at
            ):
                retry_at = acquired_at
                if policy["next_request_at"] > acquired_at:
                    retry_at = max(retry_at, rate_ready_at)
                if int(active["count"]) >= int(policy["maximum_concurrency"]):
                    retry_at = max(retry_at, concurrency_ready_at)
                return None, retry_at
            connection.execute(
                """
                INSERT INTO crawl_domain_permits (
                    permit_id, domain, worker_id, acquired_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (permit_id, domain, worker_id, acquired_at, expires_at),
            )
            next_request_at = acquired_at + timedelta(
                milliseconds=policy["minimum_interval_milliseconds"]
            )
            connection.execute(
                """
                UPDATE crawl_domain_policies
                SET next_request_at = %s, updated_at = %s WHERE domain = %s
                """,
                (next_request_at, acquired_at, domain),
            )
            return (
                DomainRequestPermit(
                    permit_id=permit_id,
                    domain=domain,
                    worker_id=worker_id,
                    acquired_at=acquired_at,
                    expires_at=expires_at,
                ),
                next_request_at,
            )

    def release_domain_permit(
        self,
        permit: DomainRequestPermit,
        *,
        released_at: datetime,
    ) -> None:
        del released_at
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                DELETE FROM crawl_domain_permits
                WHERE permit_id = %s AND domain = %s AND worker_id = %s
                """,
                (permit.permit_id, permit.domain, permit.worker_id),
            )

    def get_robots(self, origin: str, *, at: datetime) -> RobotsCacheEntry | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM crawl_robots_cache WHERE origin = %s AND expires_at > %s",
                (origin, at),
            ).fetchone()
        return _robots_from_row(row) if row is not None else None

    def store_robots(self, entry: RobotsCacheEntry) -> None:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO crawl_domain_policies (
                    domain, minimum_interval_milliseconds, maximum_concurrency,
                    next_request_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (domain) DO NOTHING
                """,
                (
                    entry.domain,
                    self._default_minimum_interval_milliseconds,
                    self._default_maximum_concurrency,
                    entry.fetched_at,
                    entry.fetched_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO crawl_robots_cache (
                    origin, domain, http_status, rules_text, fetched_at, expires_at,
                    etag, last_modified_header
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (origin) DO UPDATE SET
                    domain = EXCLUDED.domain,
                    http_status = EXCLUDED.http_status,
                    rules_text = EXCLUDED.rules_text,
                    fetched_at = EXCLUDED.fetched_at,
                    expires_at = EXCLUDED.expires_at,
                    etag = EXCLUDED.etag,
                    last_modified_header = EXCLUDED.last_modified_header
                """,
                (
                    entry.origin,
                    entry.domain,
                    entry.http_status,
                    entry.rules_text,
                    entry.fetched_at,
                    entry.expires_at,
                    entry.etag,
                    entry.last_modified,
                ),
            )

    def _lock_active_task(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        lease_token: UUID,
        *,
        at: datetime,
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT task.*, attempt.leased_at AS attempt_leased_at
            FROM crawl_fetch_tasks AS task
            JOIN crawl_fetch_attempts AS attempt ON attempt.attempt_id = task.current_attempt_id
            WHERE task.lease_token = %s
            FOR UPDATE OF task, attempt
            """,
            (lease_token,),
        ).fetchone()
        if row is None:
            raise FetchLeaseLost(f"fetch lease {lease_token} does not exist")
        if row["status"] != "leased" or row["lease_expires_at"] <= at:
            raise FetchLeaseLost("fetch lease is no longer active")
        return row

    @staticmethod
    def _require_lease_identity(row: dict[str, Any], lease: CrawlFetchLease) -> None:
        if (
            row["task_id"] != lease.task_id
            or row["frontier_id"] != lease.frontier_id
            or row["current_attempt_id"] != lease.attempt_id
            or row["attempt_count"] != lease.attempt
        ):
            raise FetchLeaseLost("fetch result identity does not match its lease")

    @staticmethod
    def _verify_public_artifact(
        connection: psycopg.Connection[dict[str, Any]],
        artifact: ArtifactReference,
    ) -> None:
        row = connection.execute(
            """
            SELECT artifact.visibility, artifact.acquisition_method,
                blob.sha256, blob.media_type, blob.byte_size
            FROM artifacts AS artifact
            JOIN artifact_blobs AS blob ON blob.sha256 = artifact.blob_sha256
            WHERE artifact.artifact_id = %s
            """,
            (artifact.artifact_id,),
        ).fetchone()
        if row is None:
            raise FetchValidationError(f"artifact {artifact.artifact_id} does not exist")
        if row["visibility"] != "public" or row["acquisition_method"] != "source_adapter":
            raise FetchValidationError("fetched content must be a public source-adapter artifact")
        if (
            row["sha256"] != artifact.sha256
            or row["media_type"] != artifact.media_type
            or row["byte_size"] != artifact.byte_size
        ):
            raise FetchValidationError("fetched artifact metadata does not match storage")

    @staticmethod
    def _finish_attempt(
        connection: psycopg.Connection[dict[str, Any]],
        row: dict[str, Any],
        *,
        outcome: CrawlFetchAttemptOutcome,
        request_url: str,
        final_url: str | None,
        http_status: int | None,
        response_media_type: str | None,
        response_sha256: str | None,
        bytes_downloaded: int | None,
        latency_milliseconds: int,
        artifact: ArtifactReference | None,
        artifact_deduplicated: bool | None,
        retry_after: datetime | None,
        error: CrawlFetchError | None,
        started_at: datetime,
        completed_at: datetime,
    ) -> None:
        connection.execute(
            """
            UPDATE crawl_fetch_attempts
            SET outcome = %s, request_url = %s, final_url = %s, http_status = %s,
                response_media_type = %s, response_sha256 = %s, bytes_downloaded = %s,
                latency_milliseconds = %s, artifact_id = %s,
                artifact_deduplicated = %s, retry_after = %s, error_json = %s,
                started_at = %s, completed_at = %s
            WHERE attempt_id = %s AND outcome = 'leased'
            """,
            (
                outcome.value,
                request_url,
                final_url,
                http_status,
                response_media_type,
                response_sha256,
                bytes_downloaded,
                latency_milliseconds,
                artifact.artifact_id if artifact is not None else None,
                artifact_deduplicated,
                retry_after,
                Jsonb(error.model_dump(mode="json")) if error is not None else None,
                started_at,
                completed_at,
                row["current_attempt_id"],
            ),
        )

    def _get_task(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        task_id: UUID,
    ) -> CrawlFetchTaskRecord:
        row = connection.execute(
            FETCH_TASK_SELECT + " WHERE task.task_id = %s",
            (task_id,),
        ).fetchone()
        if row is None:
            raise FetchTaskNotFound(f"fetch task {task_id} does not exist")
        return _task_from_row(row)


def _lease_from_values(
    row: dict[str, Any],
    *,
    worker_id: str,
    lease_token: UUID,
    attempt_id: UUID,
    attempt: int,
    leased_at: datetime,
    expires_at: datetime,
) -> CrawlFetchLease:
    return CrawlFetchLease(
        task_id=row["task_id"],
        frontier_id=row["frontier_id"],
        attempt_id=attempt_id,
        attempt=attempt,
        worker_id=worker_id,
        lease_token=lease_token,
        canonical_url=row["canonical_url"],
        domain=row["domain"],
        acquisition_method=DiscoveryAcquisitionMethod(row["acquisition_method"]),
        expected_media_types=tuple(row["expected_media_types"]),
        trace_id=row["trace_id"],
        etag=row["etag"],
        last_modified=row["last_modified_header"],
        leased_at=leased_at,
        expires_at=expires_at,
    )


def _task_from_row(row: dict[str, Any]) -> CrawlFetchTaskRecord:
    lease = None
    if row["lease_token"] is not None:
        lease = _lease_from_values(
            row,
            worker_id=row["lease_owner"],
            lease_token=row["lease_token"],
            attempt_id=row["current_attempt_id"],
            attempt=row["current_attempt_number"],
            leased_at=row["current_leased_at"],
            expires_at=row["lease_expires_at"],
        )
    result_artifact = None
    if row["result_artifact_id"] is not None:
        result_artifact = ArtifactReference(
            artifact_id=row["result_artifact_id"],
            sha256=row["result_sha256"],
            media_type=row["result_media_type"],
            byte_size=row["result_byte_size"],
        )
    return CrawlFetchTaskRecord(
        task_id=row["task_id"],
        frontier_id=row["frontier_id"],
        canonical_url=row["canonical_url"],
        domain=row["domain"],
        acquisition_method=DiscoveryAcquisitionMethod(row["acquisition_method"]),
        expected_media_types=tuple(row["expected_media_types"]),
        status=CrawlFetchTaskStatus(row["status"]),
        priority=row["priority"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        next_eligible_at=row["next_eligible_at"],
        trace_id=row["trace_id"],
        lease=lease,
        result_artifact=result_artifact,
        etag=row["etag"],
        last_modified=row["last_modified_header"],
        last_error=_error(row["last_error"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _attempt_from_row(row: dict[str, Any]) -> CrawlFetchAttemptRecord:
    artifact = None
    if row["artifact_id"] is not None:
        artifact = ArtifactReference(
            artifact_id=row["artifact_id"],
            sha256=row["artifact_sha256"],
            media_type=row["artifact_media_type"],
            byte_size=row["artifact_byte_size"],
        )
    return CrawlFetchAttemptRecord(
        attempt_id=row["attempt_id"],
        task_id=row["task_id"],
        frontier_id=row["frontier_id"],
        attempt=row["attempt_number"],
        worker_id=row["worker_id"],
        trace_id=row["trace_id"],
        outcome=CrawlFetchAttemptOutcome(row["outcome"]),
        request_url=row["request_url"],
        final_url=row["final_url"],
        http_status=row["http_status"],
        response_media_type=row["response_media_type"],
        response_sha256=row["response_sha256"],
        bytes_downloaded=row["bytes_downloaded"],
        latency_milliseconds=row["latency_milliseconds"],
        artifact=artifact,
        artifact_deduplicated=row["artifact_deduplicated"],
        retry_after=row["retry_after"],
        error=_error(row["error_json"]),
        leased_at=row["leased_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _error(value: object) -> CrawlFetchError | None:
    if value is None:
        return None
    return _ERROR_ADAPTER.validate_json(json.dumps(value), strict=True)


def _robots_from_row(row: dict[str, Any]) -> RobotsCacheEntry:
    return RobotsCacheEntry(
        origin=row["origin"],
        domain=row["domain"],
        http_status=row["http_status"],
        rules_text=row["rules_text"],
        fetched_at=row["fetched_at"],
        expires_at=row["expires_at"],
        etag=row["etag"],
        last_modified=row["last_modified_header"],
    )


def _retry_delay(attempt: int) -> timedelta:
    return timedelta(seconds=min(3600, 5 * 2 ** max(0, attempt - 1)))


def _duration_milliseconds(started_at: datetime, completed_at: datetime) -> int:
    return max(0, int((completed_at - started_at).total_seconds() * 1000))


def _domain(url: str) -> str:
    host = urlsplit(url).hostname
    if host is None:
        raise FetchValidationError("fetch task URL requires a domain")
    return host.encode("idna").decode("ascii").casefold()
