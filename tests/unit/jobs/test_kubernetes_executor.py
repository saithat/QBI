from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from hiveblot_contracts import (
    ArtifactReference,
    ArtifactVisibility,
    JobLease,
    JobStatus,
    ResourceRequirements,
)
from hiveblot_job_service import (
    JobService,
    JobWorker,
    KubernetesApiClient,
    KubernetesJobExecutor,
    KubernetesJobState,
)
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    SourceAdapterRegistry,
    VerifiedArtifactReader,
)

from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore
from tests.fakes.job_service import InMemoryJobRepository
from workers.jobs.specifications import legacy_normalization_job

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


class RecordingKubernetesClient:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.manifest: dict[str, object] | None = None
        self.deleted: list[str] = []

    def create_job(self, manifest) -> None:
        self.manifest = dict(manifest)

    def read_job(self, name: str) -> KubernetesJobState:
        del name
        assert self.manifest is not None
        metadata = self.manifest["metadata"]
        assert isinstance(metadata, dict)
        annotations = metadata["annotations"]
        assert isinstance(annotations, dict)
        attempt_id = str(annotations["hiveblot.io/attempt-id"]).replace("-", "")
        output = self.workspace_root / "attempts" / attempt_id / "outputs" / "records"
        output.write_text('[{"target":"TP53"}]\n', encoding="utf-8")
        return KubernetesJobState(
            active=False,
            succeeded=True,
            failed=False,
            exit_code=0,
            reason="Completed",
        )

    def delete_job(self, name: str) -> None:
        self.deleted.append(name)

    def read_job_log(self, name: str, limit_bytes: int) -> tuple[str, bool]:
        del name, limit_bytes
        return "normalized 40 records\n", False


def test_kubernetes_executor_uses_restricted_job_and_shared_file_contract(tmp_path: Path) -> None:
    _, reader, reference = _artifact_fixture()
    lease = _lease(reference)
    client = RecordingKubernetesClient(tmp_path)
    executor = KubernetesJobExecutor(
        reader,
        client,
        workspace_root=tmp_path,
        workspace_pvc="workspaces",
        environment={"SAFE_SETTING": "visible", "UNDECLARED": "hidden"},
        clock=lambda: NOW,
    )
    lease = lease.model_copy(
        update={
            "specification": lease.specification.model_copy(
                update={
                    "container": lease.specification.container.model_copy(
                        update={"allowed_environment_variables": ("SAFE_SETTING",)}
                    )
                }
            )
        }
    )

    outcome = executor.execute(lease, heartbeat=lambda: None, cancel_requested=lambda: False)

    assert outcome.exit_code == 0
    assert outcome.stdout == "normalized 40 records\n"
    assert outcome.outputs[0].content == b'[{"target":"TP53"}]\n'
    assert client.manifest is not None
    assert client.manifest["apiVersion"] == "batch/v1"
    specification = client.manifest["spec"]
    assert isinstance(specification, dict)
    assert specification["backoffLimit"] == 0
    assert specification["activeDeadlineSeconds"] == lease.specification.timeout_seconds
    template = specification["template"]
    assert isinstance(template, dict)
    pod = template["spec"]
    assert isinstance(pod, dict)
    assert pod["automountServiceAccountToken"] is False
    assert pod["restartPolicy"] == "Never"
    containers = pod["containers"]
    assert isinstance(containers, list)
    workload = containers[0]
    assert workload["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert workload["resources"] == {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "500m", "memory": "512Mi"},
    }
    environment = {item["name"]: item["value"] for item in workload["env"]}
    assert environment == {
        "HIVEBLOT_TRACE_ID": str(lease.specification.trace_id),
        "SAFE_SETTING": "visible",
    }
    labels = template["metadata"]["labels"]
    assert labels["hiveblot.io/network-access"] == "deny"
    assert not any(tmp_path.iterdir())


def test_kubernetes_executor_applies_optional_gpu_placement(tmp_path: Path) -> None:
    _, reader, reference = _artifact_fixture()
    lease = _lease(reference)
    lease = lease.model_copy(
        update={
            "specification": lease.specification.model_copy(
                update={
                    "resources": ResourceRequirements(
                        cpu_millicores=1000,
                        memory_mib=4096,
                        gpu_count=1,
                    )
                }
            )
        }
    )
    client = RecordingKubernetesClient(tmp_path)
    executor = KubernetesJobExecutor(
        reader,
        client,
        workspace_root=tmp_path,
        workspace_pvc="workspaces",
        gpu_node_selector={"accelerator": "nvidia"},
        gpu_tolerations=({"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"},),
        clock=lambda: NOW,
    )

    executor.execute(lease, heartbeat=lambda: None, cancel_requested=lambda: False)

    assert client.manifest is not None
    pod = client.manifest["spec"]["template"]["spec"]
    assert pod["nodeSelector"] == {"accelerator": "nvidia"}
    assert pod["tolerations"] == [
        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
    ]
    resources = pod["containers"][0]["resources"]
    assert resources["limits"]["nvidia.com/gpu"] == "1"


def test_kubernetes_executor_deletes_cancelled_job_and_workspace(tmp_path: Path) -> None:
    _, reader, reference = _artifact_fixture()
    lease = _lease(reference)
    client = RecordingKubernetesClient(tmp_path)
    executor = KubernetesJobExecutor(
        reader,
        client,
        workspace_root=tmp_path,
        workspace_pvc="workspaces",
        clock=lambda: NOW,
    )

    outcome = executor.execute(lease, heartbeat=lambda: None, cancel_requested=lambda: True)

    assert outcome.cancelled is True
    assert client.deleted == [f"hiveblot-job-{lease.attempt_id.hex[:32]}"]
    assert not any(tmp_path.iterdir())


def test_kubernetes_job_output_is_published_through_durable_job_service(tmp_path: Path) -> None:
    artifacts, reader, reference = _artifact_fixture()
    jobs = JobService(InMemoryJobRepository(), artifacts, clock=lambda: NOW)
    specification = legacy_normalization_job(
        reference,
        image="hiveblot:test",
        idempotency_key="kubernetes-publication",
        submitted_at=NOW,
    )
    jobs.submit(specification)
    client = RecordingKubernetesClient(tmp_path)
    worker = JobWorker(
        jobs,
        artifacts,
        KubernetesJobExecutor(
            reader,
            client,
            workspace_root=tmp_path,
            workspace_pvc="workspaces",
            clock=lambda: NOW,
        ),
        worker_id="scheduler-a",
        lease_seconds=60,
    )

    completed = worker.run_once()

    assert completed is not None
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.result is not None
    output = completed.result.outputs[0].artifact
    assert artifacts.get_artifact(output.artifact_id).sha256 == output.sha256
    assert jobs.list_attempts(specification.job_id)[0].executor_name == "kubernetes-job"


def test_in_cluster_client_normalizes_job_status_and_logs() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"metadata": {"name": "job-a"}})
        if request.url.path.endswith("/jobs/job-a"):
            return httpx.Response(
                200,
                json={
                    "status": {
                        "succeeded": 1,
                        "conditions": [
                            {"type": "Complete", "status": "True", "reason": "Completed"}
                        ],
                    }
                },
            )
        if request.url.path.endswith("/pods"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "metadata": {
                                "name": "job-a-pod",
                                "creationTimestamp": "2026-08-02T12:00:00Z",
                            },
                            "status": {
                                "containerStatuses": [
                                    {
                                        "name": "workload",
                                        "state": {
                                            "terminated": {
                                                "exitCode": 0,
                                                "reason": "Completed",
                                            }
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                },
            )
        if request.url.path.endswith("/pods/job-a-pod/log"):
            return httpx.Response(200, content=b"done\n")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.Client(
        base_url="https://kubernetes.test",
        transport=httpx.MockTransport(handler),
    )
    client = KubernetesApiClient(
        namespace="hiveblot",
        api_server="https://unused.test",
        token="runtime-token",
        ca_file=Path("/unused"),
        client=http_client,
    )
    manifest = {
        "metadata": {
            "name": "job-a",
            "annotations": {"hiveblot.io/attempt-id": str(uuid4())},
        }
    }

    client.create_job(manifest)
    state = client.read_job("job-a")
    content, truncated = client.read_job_log("job-a", 100)

    assert state.succeeded is True
    assert state.exit_code == 0
    assert state.reason == "Completed"
    assert content == "done\n"
    assert truncated is False
    assert requests[2].url.params["labelSelector"] == "batch.kubernetes.io/job-name=job-a"
    client.close()


def _lease(reference: ArtifactReference) -> JobLease:
    specification = legacy_normalization_job(
        reference,
        image="hiveblot:test",
        idempotency_key=f"kubernetes-{uuid4().hex}",
        submitted_at=NOW,
    )
    return JobLease(
        lease_token=uuid4(),
        job_id=specification.job_id,
        attempt_id=uuid4(),
        attempt=1,
        worker_id="worker-a",
        specification=specification,
        leased_at=NOW,
        expires_at=NOW.replace(minute=1),
    )


def _artifact_fixture() -> tuple[ArtifactService, VerifiedArtifactReader, ArtifactReference]:
    repository = InMemoryArtifactRepository()
    store = InMemoryObjectStore()
    service = ArtifactService(
        repository=repository,
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=1_000_000,
        upload_url_seconds=3600,
        download_url_seconds=900,
        clock=lambda: NOW,
    )
    content = json.dumps({"input": True}).encode()
    upload = service.begin_multipart_upload(
        original_filename="input.json",
        declared_media_type="application/json",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:test:kubernetes-input",
        relationships=(),
        actor_id=None,
    )
    session = repository.uploads[upload.upload_id]
    assert session.backend_upload_id is not None
    etag = store.put_part(session.backend_upload_id, 1, content)
    artifact = service.complete_multipart_upload(
        upload.upload_id,
        (CompletedPart(part_number=1, etag=etag),),
        actor_id=None,
    ).artifact
    reference = ArtifactReference(
        artifact_id=artifact.artifact_id,
        sha256=artifact.sha256,
        media_type=artifact.media_type,
        byte_size=artifact.byte_size,
    )
    return service, VerifiedArtifactReader(service, repository, store), reference
