from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import httpx

from workers.jobs.kubernetes_smoke import run_smoke


def test_kubernetes_smoke_uploads_submits_and_verifies_output(tmp_path: Path) -> None:
    artifact_id = "00000000-0000-0000-0000-000000000101"
    output_id = "00000000-0000-0000-0000-000000000102"
    fixture = tmp_path / "model-output.json"
    fixture.write_text('{"figure":true}', encoding="utf-8")
    submitted_job: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/artifact-uploads":
            return httpx.Response(
                201,
                json={
                    "upload_id": "00000000-0000-0000-0000-000000000100",
                    "parts": [{"part_number": 1, "url": "https://objects.test/upload"}],
                },
            )
        if request.method == "PUT" and request.url.host == "objects.test":
            return httpx.Response(200, headers={"etag": '"fixture-etag"'})
        if request.method == "POST" and request.url.path.endswith("/complete"):
            return httpx.Response(
                200,
                json={
                    "artifact": {
                        "artifact_id": artifact_id,
                        "sha256": "1" * 64,
                        "media_type": "application/json",
                        "byte_size": fixture.stat().st_size,
                    }
                },
            )
        if request.method == "POST" and request.url.path == "/api/v1/jobs":
            submitted_job.update(json.loads(request.content)["specification"])
            return httpx.Response(201, json={"status": "pending"})
        if request.method == "GET" and request.url.path.startswith("/api/v1/jobs/"):
            return httpx.Response(
                200,
                json={
                    "status": "succeeded",
                    "result": {"outputs": [{"artifact": {"artifact_id": output_id}}]},
                },
            )
        if request.method == "POST" and request.url.path.endswith("/download-url"):
            return httpx.Response(200, json={"url": "https://objects.test/output"})
        if request.method == "GET" and request.url.host == "objects.test":
            return httpx.Response(200, json=[{"record": index} for index in range(40)])
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.Client(
        base_url="http://hiveblot-api:8080",
        transport=httpx.MockTransport(handler),
    )

    job_id = run_smoke(
        api_url="http://hiveblot-api:8080",
        workload_image="hiveblot:test",
        fixture=fixture,
        timeout_seconds=5,
        client=client,
    )

    assert isinstance(job_id, UUID)
    assert submitted_job["container"]["image"] == "hiveblot:test"
    assert submitted_job["inputs"][0]["artifact"]["artifact_id"] == artifact_id
    client.close()
