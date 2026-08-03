"""Queue-backed long-lived public fetch worker."""

from __future__ import annotations

import argparse
import json
import signal
import time
from collections.abc import Sequence

from hiveblot_crawler import (
    FetchQueueService,
    FetchWorker,
    HttpFetchClient,
    PostgresFetchRepository,
    SharedDomainRateLimiter,
)
from hiveblot_storage import (
    ArtifactService,
    PostgresArtifactRepository,
    S3ObjectStore,
    SourceAdapterRegistry,
)

from hiveblot import db
from hiveblot.settings import FetchWorkerSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a HiveBlot public-source fetch worker")
    parser.add_argument("--once", action="store_true", help="attempt one queue lease and exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = FetchWorkerSettings()
    db.initialize(settings.database_url)
    object_store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    object_store.ensure_bucket()
    artifacts = ArtifactService(
        repository=PostgresArtifactRepository(settings.database_url),
        object_store=object_store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=settings.artifact_max_bytes,
        upload_url_seconds=3600,
        download_url_seconds=900,
    )
    repository = PostgresFetchRepository(
        settings.database_url,
        default_minimum_interval_milliseconds=(settings.fetch_domain_minimum_interval_milliseconds),
        default_maximum_concurrency=settings.fetch_domain_maximum_concurrency,
    )
    queue = FetchQueueService(
        repository,
        max_attempts=settings.fetch_max_attempts,
        minimum_interval_milliseconds=(settings.fetch_domain_minimum_interval_milliseconds),
        maximum_concurrency=settings.fetch_domain_maximum_concurrency,
    )
    limiter = SharedDomainRateLimiter(
        repository,
        permit_seconds=settings.fetch_domain_permit_seconds,
        maximum_wait_seconds=settings.fetch_domain_maximum_wait_seconds,
    )
    client = HttpFetchClient(
        rate_limiter=limiter,
        robots_cache=repository,
        user_agent=settings.discovery_user_agent,
        allowed_hosts=settings.fetch_allowed_hosts,
        max_response_bytes=settings.fetch_max_response_bytes,
        timeout_seconds=settings.fetch_http_timeout_seconds,
        max_redirects=settings.fetch_max_redirects,
        robots_cache_seconds=settings.fetch_robots_cache_seconds,
        robots_max_bytes=settings.fetch_robots_max_bytes,
    )
    worker = FetchWorker(
        queue,
        repository,
        client,
        artifacts,
        worker_id=settings.fetch_worker_id,
        lease_seconds=settings.fetch_lease_seconds,
    )
    try:
        if args.once:
            record = worker.run_once()
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "processed": record is not None,
                        "task_id": str(record.task_id) if record is not None else None,
                        "status": record.status.value if record is not None else None,
                    },
                    sort_keys=True,
                )
            )
            return 0

        stopping = False

        def stop(_signum: int, _frame: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        while not stopping:
            if worker.run_once() is None:
                time.sleep(settings.fetch_worker_poll_seconds)
        return 0
    finally:
        client.close()


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
