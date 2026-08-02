"""Long-lived host worker for the PRD-013 local Docker executor."""

from __future__ import annotations

import argparse
import signal
import time
from collections.abc import Sequence

from hiveblot_job_service import (
    JobService,
    JobWorker,
    LocalDockerExecutor,
    PostgresJobRepository,
    SubprocessDockerRunner,
)
from hiveblot_storage import (
    ArtifactService,
    PostgresArtifactRepository,
    S3ObjectStore,
    SourceAdapterRegistry,
    VerifiedArtifactReader,
)

from hiveblot import db
from hiveblot.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the HiveBlot local Docker job worker")
    parser.add_argument("--once", action="store_true", help="attempt one lease and exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    db.initialize(settings.database_url)
    object_store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    object_store.ensure_bucket()
    artifact_repository = PostgresArtifactRepository(settings.database_url)
    artifacts = ArtifactService(
        repository=artifact_repository,
        object_store=object_store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=settings.artifact_max_bytes,
        upload_url_seconds=settings.artifact_upload_url_seconds,
        download_url_seconds=settings.artifact_signed_url_seconds,
    )
    service = JobService(PostgresJobRepository(settings.database_url), artifacts)
    executor = LocalDockerExecutor(
        VerifiedArtifactReader(artifacts, artifact_repository, object_store),
        runner=SubprocessDockerRunner(docker_binary=settings.job_docker_binary),
        max_log_bytes=settings.job_max_log_bytes,
        max_output_bytes=settings.job_max_output_bytes,
    )
    worker = JobWorker(
        service,
        artifacts,
        executor,
        worker_id=settings.job_worker_id,
        lease_seconds=settings.job_lease_seconds,
    )
    if args.once:
        worker.run_once()
        return 0

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        if worker.run_once() is None:
            time.sleep(settings.job_worker_poll_seconds)
    return 0


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
