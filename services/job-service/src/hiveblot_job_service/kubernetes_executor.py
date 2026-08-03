"""Kubernetes Job execution over the same file contract as LocalDockerExecutor."""

from __future__ import annotations

import os
import shutil
import ssl
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import quote

import httpx
from hiveblot_contracts import JobLease, NetworkPolicy
from hiveblot_storage import ArtifactReader

from .errors import ExecutorError, InvalidExecutionOutput
from .executor import ExecutionOutcome, ExecutionOutput


@dataclass(frozen=True, slots=True)
class KubernetesJobState:
    """Normalized state required by the domain-independent executor."""

    active: bool
    succeeded: bool
    failed: bool
    exit_code: int | None = None
    reason: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        terminal_count = int(self.succeeded) + int(self.failed)
        if terminal_count > 1 or (self.active and terminal_count):
            raise ValueError("Kubernetes job state cannot be active and terminal")


class KubernetesClient(Protocol):
    """Small API boundary used by the executor and unit-test fakes."""

    def create_job(self, manifest: Mapping[str, object]) -> None: ...

    def read_job(self, name: str) -> KubernetesJobState: ...

    def delete_job(self, name: str) -> None: ...

    def read_job_log(self, name: str, limit_bytes: int) -> tuple[str, bool]: ...


class KubernetesApiClient:
    """Minimal in-cluster Kubernetes batch/v1 and Pod-log client."""

    def __init__(
        self,
        *,
        namespace: str,
        api_server: str,
        token: str,
        ca_file: Path,
        request_timeout_seconds: float = 15,
        client: httpx.Client | None = None,
    ) -> None:
        self._namespace = namespace
        if client is None:
            context = ssl.create_default_context(cafile=str(ca_file))
            client = httpx.Client(
                base_url=api_server.rstrip("/"),
                headers={"Authorization": f"Bearer {token}"},
                verify=context,
                timeout=request_timeout_seconds,
            )
        self._client = client

    @classmethod
    def from_service_account(
        cls,
        *,
        namespace: str,
        token_path: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token"),
        ca_path: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
        request_timeout_seconds: float = 15,
    ) -> KubernetesApiClient:
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "").strip()
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443").strip()
        if not host:
            raise ExecutorError(
                "KUBERNETES_SERVICE_HOST is unavailable; run the executor in a Pod",
                retryable=False,
            )
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ExecutorError(
                "Kubernetes service-account token is unavailable", retryable=False
            ) from exc
        if not token:
            raise ExecutorError("Kubernetes service-account token is empty", retryable=False)
        return cls(
            namespace=namespace,
            api_server=f"https://{host}:{port}",
            token=token,
            ca_file=ca_path,
            request_timeout_seconds=request_timeout_seconds,
        )

    def create_job(self, manifest: Mapping[str, object]) -> None:
        response = self._request(
            "POST",
            f"/apis/batch/v1/namespaces/{quote(self._namespace, safe='')}/jobs",
            json_payload=manifest,
            accepted={201, 409},
        )
        if response.status_code != 409:
            return
        metadata = cast(Mapping[str, object], manifest["metadata"])
        name = str(metadata["name"])
        existing = self._request_json(
            "GET",
            self._job_path(name),
            accepted={200},
        )
        expected_annotations = cast(Mapping[str, object], metadata.get("annotations", {}))
        actual_metadata = _mapping(existing.get("metadata"))
        actual_annotations = _mapping(actual_metadata.get("annotations"))
        expected_attempt = expected_annotations.get("hiveblot.io/attempt-id")
        if actual_annotations.get("hiveblot.io/attempt-id") != expected_attempt:
            raise ExecutorError("Kubernetes Job name collision", retryable=False)

    def read_job(self, name: str) -> KubernetesJobState:
        job = self._request_json("GET", self._job_path(name), accepted={200})
        status = _mapping(job.get("status"))
        condition_type, reason, message = _terminal_condition(status)
        succeeded = condition_type == "Complete"
        failed = condition_type == "Failed"
        active = not succeeded and not failed
        exit_code: int | None = None
        if succeeded or failed:
            pod = self._latest_job_pod(name)
            exit_code, pod_reason, pod_message = _container_termination(pod)
            reason = pod_reason or reason
            message = pod_message or message
        return KubernetesJobState(
            active=active,
            succeeded=succeeded,
            failed=failed,
            exit_code=exit_code,
            reason=reason,
            message=message,
        )

    def delete_job(self, name: str) -> None:
        self._request(
            "DELETE",
            self._job_path(name),
            params={"propagationPolicy": "Background"},
            accepted={200, 202, 404},
        )

    def read_job_log(self, name: str, limit_bytes: int) -> tuple[str, bool]:
        pod = self._latest_job_pod(name)
        metadata = _mapping(pod.get("metadata"))
        pod_name = str(metadata.get("name", ""))
        if not pod_name:
            return "", False
        response = self._request(
            "GET",
            (
                f"/api/v1/namespaces/{quote(self._namespace, safe='')}/pods/"
                f"{quote(pod_name, safe='')}/log"
            ),
            params={"container": "workload", "limitBytes": str(limit_bytes + 1)},
            accepted={200, 404},
        )
        if response.status_code == 404:
            return "", False
        content = response.content
        truncated = len(content) > limit_bytes
        return content[:limit_bytes].decode("utf-8", errors="replace"), truncated

    def close(self) -> None:
        self._client.close()

    def _latest_job_pod(self, name: str) -> Mapping[str, object]:
        result = self._request_json(
            "GET",
            f"/api/v1/namespaces/{quote(self._namespace, safe='')}/pods",
            params={"labelSelector": f"batch.kubernetes.io/job-name={name}"},
            accepted={200},
        )
        items = result.get("items", [])
        if not isinstance(items, list) or not items:
            return {}
        pods = [item for item in items if isinstance(item, dict)]
        return max(
            pods,
            key=lambda item: str(_mapping(item.get("metadata")).get("creationTimestamp", "")),
        )

    def _job_path(self, name: str) -> str:
        return (
            f"/apis/batch/v1/namespaces/{quote(self._namespace, safe='')}/jobs/"
            f"{quote(name, safe='')}"
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        accepted: set[int],
        params: Mapping[str, str] | None = None,
        json_payload: object | None = None,
    ) -> Mapping[str, object]:
        response = self._request(
            method,
            path,
            accepted=accepted,
            params=params,
            json_payload=json_payload,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExecutorError("Kubernetes API returned invalid JSON", retryable=True) from exc
        if not isinstance(payload, dict):
            raise ExecutorError("Kubernetes API returned a non-object response", retryable=True)
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        accepted: set[int],
        params: Mapping[str, str] | None = None,
        json_payload: object | None = None,
    ) -> httpx.Response:
        try:
            if json_payload is None:
                response = self._client.request(method, path, params=params)
            else:
                response = self._client.request(
                    method,
                    path,
                    params=params,
                    json=json_payload,
                )
        except httpx.HTTPError as exc:
            raise ExecutorError("Kubernetes API request failed", retryable=True) from exc
        if response.status_code not in accepted:
            retryable = response.status_code >= 500 or response.status_code in {408, 429}
            raise ExecutorError(
                f"Kubernetes API returned HTTP {response.status_code}",
                retryable=retryable,
            )
        return response


class KubernetesJobExecutor:
    """Execute a leased job as one bounded Kubernetes Job."""

    name = "kubernetes-job"

    def __init__(
        self,
        artifact_reader: ArtifactReader,
        client: KubernetesClient,
        *,
        workspace_root: Path,
        workspace_pvc: str,
        service_account_name: str = "hiveblot-job-runner",
        environment: Mapping[str, str] | None = None,
        poll_seconds: float = 1,
        ttl_seconds_after_finished: int = 3600,
        max_log_bytes: int = 262_144,
        max_output_bytes: int = 1_073_741_824,
        gpu_node_selector: Mapping[str, str] | None = None,
        gpu_tolerations: Sequence[Mapping[str, str]] = (),
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._artifact_reader = artifact_reader
        self._client = client
        self._workspace_root = workspace_root
        self._workspace_pvc = workspace_pvc
        self._service_account_name = service_account_name
        self._environment = dict(environment or {})
        self._poll_seconds = poll_seconds
        self._ttl_seconds = ttl_seconds_after_finished
        self._max_log_bytes = max_log_bytes
        self._max_output_bytes = max_output_bytes
        self._gpu_node_selector = dict(gpu_node_selector or {})
        self._gpu_tolerations = tuple(dict(item) for item in gpu_tolerations)
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        lease: JobLease,
        *,
        heartbeat: Callable[[], None],
        cancel_requested: Callable[[], bool],
    ) -> ExecutionOutcome:
        relative_root = Path("attempts") / lease.attempt_id.hex
        root = self._workspace_root / relative_root
        inputs = root / "inputs"
        outputs = root / "outputs"
        root.mkdir(mode=0o750, parents=True, exist_ok=False)
        inputs.mkdir(mode=0o750)
        outputs.mkdir(mode=0o770)
        outputs.chmod(0o770)
        job_name = f"hiveblot-job-{lease.attempt_id.hex[:32]}"
        started_at = self._clock()
        safe_to_remove = False
        try:
            self._materialize_inputs(lease, inputs)
            manifest = self._manifest(lease, job_name, relative_root)
            self._client.create_job(manifest)
            deadline = self._monotonic() + lease.specification.timeout_seconds
            while True:
                heartbeat()
                if cancel_requested():
                    self._client.delete_job(job_name)
                    safe_to_remove = True
                    return self._outcome(
                        started_at=started_at,
                        exit_code=143,
                        cancelled=True,
                    )
                if self._monotonic() >= deadline:
                    self._client.delete_job(job_name)
                    safe_to_remove = True
                    return self._outcome(
                        started_at=started_at,
                        exit_code=124,
                        timed_out=True,
                    )
                state = self._client.read_job(job_name)
                if state.succeeded or state.failed:
                    stdout, stdout_truncated = self._client.read_job_log(
                        job_name,
                        self._max_log_bytes,
                    )
                    safe_to_remove = True
                    stderr = ""
                    if state.failed:
                        stderr = ": ".join(item for item in (state.reason, state.message) if item)
                    return ExecutionOutcome(
                        exit_code=state.exit_code
                        if state.exit_code is not None
                        else int(state.failed),
                        started_at=started_at,
                        completed_at=self._clock(),
                        cancelled=False,
                        timed_out=False,
                        stdout=stdout,
                        stderr=stderr,
                        stdout_truncated=stdout_truncated,
                        stderr_truncated=False,
                        outputs=self._read_outputs(lease, outputs) if state.succeeded else (),
                    )
                self._sleeper(self._poll_seconds)
        finally:
            if safe_to_remove:
                _remove_workspace(root)
                with suppress(OSError):
                    root.parent.rmdir()

    def _outcome(
        self,
        *,
        started_at: datetime,
        exit_code: int,
        cancelled: bool = False,
        timed_out: bool = False,
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            exit_code=exit_code,
            started_at=started_at,
            completed_at=self._clock(),
            cancelled=cancelled,
            timed_out=timed_out,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            outputs=(),
        )

    def _materialize_inputs(self, lease: JobLease, directory: Path) -> None:
        for named in lease.specification.inputs:
            content = self._artifact_reader.read_bytes(named.artifact.artifact_id)
            if len(content) != named.artifact.byte_size:
                raise ExecutorError(
                    "verified artifact reader returned the wrong size",
                    retryable=False,
                )
            path = directory / named.name
            path.write_bytes(content)
            path.chmod(0o440)
        directory.chmod(0o550)

    def _read_outputs(self, lease: JobLease, directory: Path) -> tuple[ExecutionOutput, ...]:
        expected = {item.name: item for item in lease.specification.expected_outputs}
        present_names = {path.name for path in directory.iterdir()}
        if present_names - expected.keys():
            raise InvalidExecutionOutput("container created an undeclared output")
        total_bytes = 0
        result: list[ExecutionOutput] = []
        for name, declaration in expected.items():
            path = directory / name
            if not path.exists():
                if declaration.required:
                    raise InvalidExecutionOutput(
                        f"container did not create required output {name!r}"
                    )
                continue
            if path.is_symlink() or not path.is_file():
                raise InvalidExecutionOutput(f"job output {name!r} must be a regular file")
            size = path.stat().st_size
            total_bytes += size
            if size > self._max_output_bytes or total_bytes > self._max_output_bytes:
                raise InvalidExecutionOutput("job output exceeds the configured size limit")
            result.append(
                ExecutionOutput(
                    name=name,
                    media_type=declaration.media_type,
                    content=path.read_bytes(),
                )
            )
        return tuple(result)

    def _manifest(
        self,
        lease: JobLease,
        name: str,
        relative_root: Path,
    ) -> dict[str, object]:
        specification = lease.specification
        resources: dict[str, str] = {
            "cpu": f"{specification.resources.cpu_millicores}m",
            "memory": f"{specification.resources.memory_mib}Mi",
        }
        if specification.resources.gpu_count:
            resources["nvidia.com/gpu"] = str(specification.resources.gpu_count)
        environment = {
            variable: self._environment[variable]
            for variable in specification.container.allowed_environment_variables
            if variable in self._environment
        }
        environment["HIVEBLOT_TRACE_ID"] = str(specification.trace_id)
        labels = {
            "app.kubernetes.io/name": "hiveblot-job",
            "app.kubernetes.io/component": "bounded-workload",
            "hiveblot.io/job-id": str(lease.job_id),
            "hiveblot.io/attempt-id": str(lease.attempt_id),
            "hiveblot.io/network-access": (
                "allow" if specification.container.network_policy is NetworkPolicy.ALLOW else "deny"
            ),
        }
        pod_spec: dict[str, object] = {
            "serviceAccountName": self._service_account_name,
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "enableServiceLinks": False,
            "terminationGracePeriodSeconds": 10,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "runAsGroup": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "workload",
                    "image": specification.container.image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": list(specification.container.command),
                    "args": list(specification.container.arguments),
                    "env": [
                        {"name": variable, "value": value}
                        for variable, value in sorted(environment.items())
                    ],
                    "resources": {"requests": resources, "limits": resources},
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [
                        {
                            "name": "workspace",
                            "mountPath": "/inputs",
                            "subPath": str(relative_root / "inputs"),
                            "readOnly": True,
                        },
                        {
                            "name": "workspace",
                            "mountPath": "/outputs",
                            "subPath": str(relative_root / "outputs"),
                        },
                        {"name": "tmp", "mountPath": "/tmp"},
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "workspace",
                    "persistentVolumeClaim": {"claimName": self._workspace_pvc},
                },
                {
                    "name": "tmp",
                    "emptyDir": {
                        "sizeLimit": (
                            f"{max(16, min(512, specification.resources.memory_mib // 4))}Mi"
                        )
                    },
                },
            ],
        }
        if specification.resources.gpu_count and self._gpu_node_selector:
            pod_spec["nodeSelector"] = self._gpu_node_selector
        if specification.resources.gpu_count and self._gpu_tolerations:
            pod_spec["tolerations"] = list(self._gpu_tolerations)
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "labels": labels,
                "annotations": {
                    "hiveblot.io/trace-id": str(specification.trace_id),
                    "hiveblot.io/attempt-id": str(lease.attempt_id),
                },
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": specification.timeout_seconds,
                "ttlSecondsAfterFinished": self._ttl_seconds,
                "template": {"metadata": {"labels": labels}, "spec": pod_spec},
            },
        }


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _terminal_condition(
    status: Mapping[str, Any],
) -> tuple[str | None, str | None, str | None]:
    conditions = status.get("conditions", [])
    if not isinstance(conditions, list):
        return None, None, None
    for condition in reversed(conditions):
        if not isinstance(condition, dict) or condition.get("status") != "True":
            continue
        if condition.get("type") in {"Complete", "Failed"}:
            return (
                str(condition["type"]),
                _optional_string(condition.get("reason")),
                _optional_string(condition.get("message")),
            )
    return None, None, None


def _container_termination(pod: Mapping[str, object]) -> tuple[int | None, str | None, str | None]:
    status = _mapping(pod.get("status"))
    container_statuses = status.get("containerStatuses", [])
    if not isinstance(container_statuses, list):
        return None, None, None
    for container in container_statuses:
        if not isinstance(container, dict) or container.get("name") != "workload":
            continue
        terminated = _mapping(_mapping(container.get("state")).get("terminated"))
        exit_code = terminated.get("exitCode")
        return (
            int(exit_code) if isinstance(exit_code, int) else None,
            _optional_string(terminated.get("reason")),
            _optional_string(terminated.get("message")),
        )
    return None, None, None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _remove_workspace(root: Path) -> None:
    for directory, child_directories, _files in os.walk(root, topdown=False):
        for child in child_directories:
            path = Path(directory) / child
            if not path.is_symlink():
                path.chmod(0o700)
        Path(directory).chmod(0o700)
    shutil.rmtree(root, ignore_errors=True)
