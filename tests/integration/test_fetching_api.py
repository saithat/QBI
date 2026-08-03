from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from hiveblot_contracts import (
    CrawlFetchAttemptOutcome,
    CrawlFetchAttemptRecord,
    CrawlFetchError,
    CrawlFetchTaskRecord,
    CrawlFetchTaskStatus,
    CrawlMetricsSnapshot,
    DiscoveryAcquisitionMethod,
)
from hiveblot_crawler import FetchTaskNotFound

from apps.api.application import app
from apps.api.discovery_dependencies import get_fetch_queue_service

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
TRACE_ID = UUID("00000000-0000-0000-0000-000000000123")


class StubFetchQueue:
    def __init__(self, task: CrawlFetchTaskRecord, attempt: CrawlFetchAttemptRecord) -> None:
        self.task = task
        self.attempt = attempt

    def get_task(self, task_id: UUID) -> CrawlFetchTaskRecord:
        if task_id != self.task.task_id:
            raise FetchTaskNotFound(f"fetch task {task_id} does not exist")
        return self.task

    def list_attempts(self, task_id: UUID) -> tuple[CrawlFetchAttemptRecord, ...]:
        self.get_task(task_id)
        return (self.attempt,)

    def metrics(self) -> CrawlMetricsSnapshot:
        return CrawlMetricsSnapshot(
            measured_at=NOW,
            queue_depth=7,
            leased_tasks=1,
            active_workers=1,
            retry_count=2,
            dead_letter_count=3,
            completed_fetches=4,
            bytes_downloaded=100,
            deduplicated_artifacts=1,
            fetch_latency_milliseconds_sum=50,
            http_status_counts={"429": 2},
            source_error_counts={"http_429": 2},
        )


def test_fetch_tasks_attempts_metrics_and_missing_task_are_exposed() -> None:
    task_id = uuid4()
    frontier_id = uuid4()
    attempt_id = uuid4()
    error = CrawlFetchError(code="http_429", message="retry later", retryable=True)
    task = CrawlFetchTaskRecord(
        task_id=task_id,
        frontier_id=frontier_id,
        canonical_url="https://source.test/paper.pdf",
        domain="source.test",
        acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
        expected_media_types=("application/pdf",),
        status=CrawlFetchTaskStatus.RETRY_WAIT,
        priority=100,
        attempt_count=1,
        max_attempts=3,
        next_eligible_at=NOW,
        trace_id=TRACE_ID,
        last_error=error,
        created_at=NOW,
        updated_at=NOW,
    )
    attempt = CrawlFetchAttemptRecord(
        attempt_id=attempt_id,
        task_id=task_id,
        frontier_id=frontier_id,
        attempt=1,
        worker_id="worker-1",
        trace_id=TRACE_ID,
        outcome=CrawlFetchAttemptOutcome.RETRYABLE_FAILURE,
        request_url=task.canonical_url,
        final_url=task.canonical_url,
        http_status=429,
        latency_milliseconds=50,
        error=error,
        leased_at=NOW,
        started_at=NOW,
        completed_at=NOW,
    )
    app.dependency_overrides[get_fetch_queue_service] = lambda: StubFetchQueue(task, attempt)
    client = TestClient(app)
    try:
        task_response = client.get(f"/api/v1/crawl-fetch-tasks/{task_id}")
        assert task_response.status_code == 200
        assert task_response.json()["status"] == "retry_wait"

        attempts = client.get(f"/api/v1/crawl-fetch-tasks/{task_id}/attempts")
        assert attempts.status_code == 200
        assert attempts.json()[0]["error"]["code"] == "http_429"

        metrics = client.get("/api/v1/crawl/metrics")
        assert metrics.status_code == 200
        assert metrics.json()["queue_depth"] == 7

        missing = client.get(f"/api/v1/crawl-fetch-tasks/{uuid4()}")
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.pop(get_fetch_queue_service, None)
