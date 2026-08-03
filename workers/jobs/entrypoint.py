"""Long-lived host worker for the PRD-013 local Docker executor."""

from __future__ import annotations

import argparse
import os
import signal
import time
from collections.abc import Mapping, Sequence

from hiveblot_job_service import (
    JobExecutor,
    JobService,
    JobWorker,
    KubernetesApiClient,
    KubernetesJobExecutor,
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
from hiveblot.settings import JobExecutorKind, get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a HiveBlot generic job worker")
    parser.add_argument("--once", action="store_true", help="attempt one lease and exit")
    parser.add_argument(
        "--executor",
        choices=tuple(item.value for item in JobExecutorKind),
        help="override JOB_EXECUTOR",
    )
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
    artifact_reader = VerifiedArtifactReader(artifacts, artifact_repository, object_store)
    executor_kind = JobExecutorKind(args.executor) if args.executor else settings.job_executor
    executor: JobExecutor
    if executor_kind is JobExecutorKind.LOCAL_DOCKER:
        executor = LocalDockerExecutor(
            artifact_reader,
            runner=SubprocessDockerRunner(docker_binary=settings.job_docker_binary),
            environment=os.environ,
            max_log_bytes=settings.job_max_log_bytes,
            max_output_bytes=settings.job_max_output_bytes,
        )
    else:
        selector = {}
        if settings.kubernetes_gpu_node_selector_key is not None:
            assert settings.kubernetes_gpu_node_selector_value is not None
            selector[settings.kubernetes_gpu_node_selector_key] = (
                settings.kubernetes_gpu_node_selector_value
            )
        tolerations: tuple[Mapping[str, str], ...] = ()
        if settings.kubernetes_gpu_toleration_key is not None:
            tolerations = (
                {
                    "key": settings.kubernetes_gpu_toleration_key,
                    "operator": "Exists",
                    "effect": settings.kubernetes_gpu_toleration_effect,
                },
            )
        executor = KubernetesJobExecutor(
            artifact_reader,
            KubernetesApiClient.from_service_account(
                namespace=settings.kubernetes_job_namespace,
                request_timeout_seconds=settings.kubernetes_api_timeout_seconds,
            ),
            workspace_root=settings.kubernetes_workspace_root,
            workspace_pvc=settings.kubernetes_workspace_pvc,
            service_account_name=settings.kubernetes_job_service_account,
            environment=os.environ,
            poll_seconds=settings.kubernetes_poll_seconds,
            ttl_seconds_after_finished=settings.kubernetes_job_ttl_seconds,
            max_log_bytes=settings.job_max_log_bytes,
            max_output_bytes=settings.job_max_output_bytes,
            gpu_node_selector=selector,
            gpu_tolerations=tolerations,
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
