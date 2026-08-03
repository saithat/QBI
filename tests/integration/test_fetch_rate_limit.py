"""Simulated-source acceptance test for replica-independent domain limits."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from time import monotonic
from uuid import uuid4

from hiveblot_contracts import CrawlFetchLease, DiscoveryAcquisitionMethod
from hiveblot_crawler import HttpFetchClient, SharedDomainRateLimiter

from tests.fakes.fetching import InMemoryDomainPermitRepository, InMemoryRobotsCache


def test_multiple_workers_share_one_source_start_rate() -> None:
    arrivals: list[float] = []
    arrival_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            with arrival_lock:
                arrivals.append(monotonic())
            content = b"%PDF-rate-limit-fixture"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    repository = InMemoryDomainPermitRepository(
        minimum_interval_milliseconds=150,
        maximum_concurrency=10,
    )

    def fetch(index: int) -> bytes:
        limiter = SharedDomainRateLimiter(repository, maximum_wait_seconds=2)
        client = HttpFetchClient(
            rate_limiter=limiter,
            robots_cache=InMemoryRobotsCache(),
            user_agent="HiveBlotRateTest/1.0 (+https://example.test/contact)",
            allowed_hosts=("127.0.0.1",),
            max_response_bytes=1024,
            timeout_seconds=2,
        )
        now = datetime.now(UTC)
        try:
            result = client.fetch(
                CrawlFetchLease(
                    task_id=uuid4(),
                    frontier_id=uuid4(),
                    attempt_id=uuid4(),
                    attempt=1,
                    worker_id=f"worker-{index}",
                    lease_token=uuid4(),
                    canonical_url=f"http://127.0.0.1:{server.server_port}/{index}.pdf",
                    domain="127.0.0.1",
                    acquisition_method=DiscoveryAcquisitionMethod.OFFICIAL_API,
                    expected_media_types=("application/pdf",),
                    trace_id=uuid4(),
                    leased_at=now,
                    expires_at=now + timedelta(minutes=1),
                )
            )
            return result.content
        finally:
            client.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(fetch, (1, 2)))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert results == (b"%PDF-rate-limit-fixture", b"%PDF-rate-limit-fixture")
    assert len(arrivals) == 2
    assert abs(arrivals[1] - arrivals[0]) >= 0.1
