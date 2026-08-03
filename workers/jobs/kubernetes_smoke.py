"""End-to-end smoke client for a deployed Kubernetes runtime."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from hiveblot_contracts import ArtifactReference, JobStatus

from .specifications import legacy_normalization_job

TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
    JobStatus.DEAD_LETTER.value,
}


def run_smoke(
    *,
    api_url: str,
    workload_image: str,
    fixture: Path,
    timeout_seconds: int,
    client: httpx.Client | None = None,
) -> UUID:
    content = fixture.read_bytes()
    deadline = time.monotonic() + timeout_seconds
    runtime_client = client or httpx.Client(base_url=api_url.rstrip("/"), timeout=30)
    try:
        begin = runtime_client.post(
            "/api/v1/artifact-uploads",
            json={
                "schema_version": "1.0",
                "original_filename": fixture.name,
                "declared_media_type": "application/json",
                "expected_byte_size": len(content),
                "part_count": 1,
                "source_uri": "urn:hiveblot:kubernetes-smoke:model-output",
                "visibility": "public",
                "relationships": [],
            },
        )
        begin.raise_for_status()
        upload = begin.json()
        upload_id = UUID(upload["upload_id"])
        part = runtime_client.put(upload["parts"][0]["url"], content=content)
        part.raise_for_status()
        etag = part.headers.get("etag", "")
        if not etag:
            raise RuntimeError("object store did not return a multipart ETag")
        complete = runtime_client.post(
            f"/api/v1/artifact-uploads/{upload_id}/complete",
            json={
                "schema_version": "1.0",
                "parts": [{"schema_version": "1.0", "part_number": 1, "etag": etag}],
            },
        )
        complete.raise_for_status()
        artifact = complete.json()["artifact"]
        reference = ArtifactReference(
            artifact_id=UUID(artifact["artifact_id"]),
            sha256=artifact["sha256"],
            media_type=artifact["media_type"],
            byte_size=artifact["byte_size"],
        )
        specification = legacy_normalization_job(
            reference,
            image=workload_image,
            idempotency_key=f"kubernetes-smoke-{uuid4().hex}",
        )
        submitted = runtime_client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "specification": specification.model_dump(mode="json"),
            },
        )
        submitted.raise_for_status()
        while time.monotonic() < deadline:
            detail = runtime_client.get(f"/api/v1/jobs/{specification.job_id}")
            detail.raise_for_status()
            record = detail.json()
            if record["status"] in TERMINAL_STATUSES:
                if record["status"] != JobStatus.SUCCEEDED.value:
                    raise RuntimeError(f"Kubernetes smoke job ended as {record['status']}")
                output = record["result"]["outputs"][0]["artifact"]
                signed = runtime_client.post(
                    f"/api/v1/artifacts/{output['artifact_id']}/download-url"
                )
                signed.raise_for_status()
                downloaded = runtime_client.get(signed.json()["url"])
                downloaded.raise_for_status()
                normalized = json.loads(downloaded.content)
                if not isinstance(normalized, list) or len(normalized) != 40:
                    raise RuntimeError("Kubernetes smoke output did not preserve 40 records")
                return specification.job_id
            time.sleep(1)
        raise TimeoutError("timed out waiting for the Kubernetes smoke job")
    finally:
        if client is None:
            runtime_client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://hiveblot-api:8080")
    parser.add_argument("--workload-image", default="hiveblot:prd-014")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/baseline/oduah_2024_page_4_model_output.json"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    job_id = run_smoke(
        api_url=arguments.api_url,
        workload_image=arguments.workload_image,
        fixture=arguments.fixture,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps({"job_id": str(job_id), "status": "succeeded"}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
