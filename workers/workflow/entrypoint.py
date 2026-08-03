"""Temporal worker process for durable HiveBlot platform workflows."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker

from hiveblot.settings import get_settings


@workflow.defn(name="hiveblot.runtime-probe.v1")
class RuntimeProbeWorkflow:
    """Minimal registered workflow used to verify worker/runtime compatibility."""

    @workflow.run
    async def run(self) -> str:
        return "ok"


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Run the HiveBlot Temporal workflow worker")


async def run_worker() -> None:
    settings = get_settings()
    api_key = settings.temporal_api_key.get_secret_value() or None
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        tls=settings.temporal_tls,
        api_key=api_key,
    )
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[RuntimeProbeWorkflow],
    )
    await worker.run()


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    asyncio.run(run_worker())
    return 0


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
