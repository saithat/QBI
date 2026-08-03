"""Long-lived worker orchestration around the generic job service."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePath
from uuid import UUID

from hiveblot_contracts import (
    ArtifactReference,
    ArtifactRelationship,
    ArtifactRelationshipKind,
    ArtifactVisibility,
    CancelledJobResult,
    FailedJobResult,
    JobError,
    JobLease,
    JobLogStream,
    JobRecord,
    JobResult,
    NamedArtifactReference,
    SucceededJobResult,
)
from hiveblot_storage import ArtifactService, ArtifactStorageError, InvalidArtifact

from .errors import ExecutorError, InvalidJobState
from .executor import ExecutionOutcome, JobExecutor
from .service import JobService


class JobWorker:
    """Lease one job, execute it, publish artifacts, and commit one terminal result."""

    def __init__(
        self,
        service: JobService,
        artifacts: ArtifactService,
        executor: JobExecutor,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        self._service = service
        self._artifacts = artifacts
        self._executor = executor
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds

    def run_once(self) -> JobRecord | None:
        lease = self._service.lease_next(
            worker_id=self._worker_id,
            executor_name=self._executor.name,
            lease_seconds=self._lease_seconds,
        )
        if lease is None:
            return None
        attempt = self._service.mark_running(lease.lease_token)
        result: JobResult
        try:

            def heartbeat() -> None:
                self._service.heartbeat(
                    lease.lease_token,
                    lease_seconds=self._lease_seconds,
                )

            outcome = self._executor.execute(
                lease,
                heartbeat=heartbeat,
                cancel_requested=lambda: self._service.is_cancel_requested(lease.job_id),
            )
        except ExecutorError as exc:
            completed_at = datetime.now(UTC)
            result = FailedJobResult(
                job_id=lease.job_id,
                attempt=lease.attempt,
                trace_id=lease.specification.trace_id,
                started_at=attempt.started_at or attempt.leased_at,
                completed_at=completed_at,
                error=JobError(
                    code="executor_error",
                    message=str(exc),
                    retryable=exc.retryable,
                ),
            )
            self._service.append_log(
                lease.attempt_id,
                sequence=1,
                stream=JobLogStream.SYSTEM,
                content=str(exc),
                truncated=False,
            )
            return self._publish_result(lease, result)

        try:
            self._publish_logs(lease, outcome)
            if outcome.cancelled:
                reason = (
                    self._service.get_job(lease.job_id).cancellation_reason or "worker cancelled"
                )
                result = CancelledJobResult(
                    job_id=lease.job_id,
                    attempt=lease.attempt,
                    trace_id=lease.specification.trace_id,
                    started_at=outcome.started_at,
                    completed_at=outcome.completed_at,
                    reason=reason,
                )
            elif outcome.timed_out:
                result = FailedJobResult(
                    job_id=lease.job_id,
                    attempt=lease.attempt,
                    trace_id=lease.specification.trace_id,
                    started_at=outcome.started_at,
                    completed_at=outcome.completed_at,
                    error=JobError(
                        code="job_timeout",
                        message=(
                            f"container exceeded timeout of "
                            f"{lease.specification.timeout_seconds} seconds"
                        ),
                        retryable=True,
                    ),
                )
            elif outcome.exit_code != 0:
                result = FailedJobResult(
                    job_id=lease.job_id,
                    attempt=lease.attempt,
                    trace_id=lease.specification.trace_id,
                    started_at=outcome.started_at,
                    completed_at=outcome.completed_at,
                    error=JobError(
                        code="container_exit",
                        message=f"container exited with status {outcome.exit_code}",
                        retryable=True,
                    ),
                )
            else:
                result = SucceededJobResult(
                    job_id=lease.job_id,
                    attempt=lease.attempt,
                    trace_id=lease.specification.trace_id,
                    started_at=outcome.started_at,
                    completed_at=outcome.completed_at,
                    outputs=self._publish_outputs(lease, outcome),
                )
        except (ExecutorError, ArtifactStorageError) as exc:
            result = FailedJobResult(
                job_id=lease.job_id,
                attempt=lease.attempt,
                trace_id=lease.specification.trace_id,
                started_at=outcome.started_at,
                completed_at=datetime.now(UTC),
                error=JobError(
                    code="output_publication_error",
                    message=str(exc),
                    retryable=(
                        exc.retryable
                        if isinstance(exc, ExecutorError)
                        else not isinstance(exc, InvalidArtifact)
                    ),
                ),
            )
        return self._publish_result(lease, result)

    def _publish_result(self, lease: JobLease, result: JobResult) -> JobRecord:
        try:
            return self._service.publish_result(lease.lease_token, result)
        except InvalidJobState:
            if result.status == "cancelled" or not self._service.is_cancel_requested(lease.job_id):
                raise
            cancellation = CancelledJobResult(
                job_id=lease.job_id,
                attempt=lease.attempt,
                trace_id=lease.specification.trace_id,
                started_at=result.started_at,
                completed_at=datetime.now(UTC),
                reason=(
                    self._service.get_job(lease.job_id).cancellation_reason
                    or "worker cancellation race"
                ),
            )
            return self._service.publish_result(lease.lease_token, cancellation)

    def _publish_logs(self, lease: JobLease, outcome: ExecutionOutcome) -> None:
        if outcome.stdout or outcome.stdout_truncated:
            self._service.append_log(
                lease.attempt_id,
                sequence=1,
                stream=JobLogStream.STDOUT,
                content=outcome.stdout,
                truncated=outcome.stdout_truncated,
            )
        if outcome.stderr or outcome.stderr_truncated:
            self._service.append_log(
                lease.attempt_id,
                sequence=1,
                stream=JobLogStream.STDERR,
                content=outcome.stderr,
                truncated=outcome.stderr_truncated,
            )

    def _publish_outputs(
        self,
        lease: JobLease,
        outcome: ExecutionOutcome,
    ) -> tuple[NamedArtifactReference, ...]:
        visibility, organization_id = self._output_scope(lease)
        relationships = tuple(
            ArtifactRelationship(
                related_artifact_id=item.artifact.artifact_id,
                kind=ArtifactRelationshipKind.DERIVED_FROM,
            )
            for item in lease.specification.inputs
        )
        published: list[NamedArtifactReference] = []
        for output in outcome.outputs:
            artifact = self._artifacts.publish_tool_output(
                original_filename=PurePath(output.name).name,
                declared_media_type=output.media_type,
                content=output.content,
                source_uri=(
                    f"urn:hiveblot:job:{lease.job_id}:attempt:{lease.attempt}:output:{output.name}"
                ),
                visibility=visibility,
                organization_id=organization_id,
                relationships=relationships,
                actor_id=None,
            ).artifact
            published.append(
                NamedArtifactReference(
                    name=output.name,
                    artifact=ArtifactReference(
                        artifact_id=artifact.artifact_id,
                        sha256=artifact.sha256,
                        media_type=artifact.media_type,
                        byte_size=artifact.byte_size,
                    ),
                )
            )
        return tuple(published)

    def _output_scope(self, lease: JobLease) -> tuple[ArtifactVisibility, UUID | None]:
        return lease.specification.visibility, lease.specification.organization_id
