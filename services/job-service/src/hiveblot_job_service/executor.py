"""Restricted local Docker execution for finite generic jobs."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from hiveblot_contracts import JobLease, NetworkPolicy
from hiveblot_storage import ArtifactReader

from .errors import ExecutorError, InvalidExecutionOutput


@dataclass(frozen=True, slots=True)
class DockerRunRequest:
    container_name: str
    image: str
    command: tuple[str, ...]
    arguments: tuple[str, ...]
    input_directory: Path
    output_directory: Path
    environment: Mapping[str, str]
    cpu_millicores: int
    memory_mib: int
    network_policy: NetworkPolicy
    timeout_seconds: int
    stdout_path: Path
    stderr_path: Path


@dataclass(frozen=True, slots=True)
class DockerRunResult:
    exit_code: int
    started_at: datetime
    completed_at: datetime
    cancelled: bool
    timed_out: bool


class DockerRunner(Protocol):
    def run(
        self,
        request: DockerRunRequest,
        *,
        heartbeat: Callable[[], None],
        cancel_requested: Callable[[], bool],
    ) -> DockerRunResult: ...


@dataclass(frozen=True, slots=True)
class ExecutionOutput:
    name: str
    media_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    exit_code: int
    started_at: datetime
    completed_at: datetime
    cancelled: bool
    timed_out: bool
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    outputs: tuple[ExecutionOutput, ...]


class JobExecutor(Protocol):
    name: str

    def execute(
        self,
        lease: JobLease,
        *,
        heartbeat: Callable[[], None],
        cancel_requested: Callable[[], bool],
    ) -> ExecutionOutcome: ...


class SubprocessDockerRunner:
    """Run Docker through an argv-only subprocess and poll for durable control signals."""

    def __init__(
        self,
        *,
        docker_binary: str = "docker",
        poll_seconds: float = 1.0,
        stop_grace_seconds: int = 5,
        host_environment: Mapping[str, str] | None = None,
    ) -> None:
        self._docker_binary = docker_binary
        self._poll_seconds = poll_seconds
        self._stop_grace_seconds = stop_grace_seconds
        self._host_environment = dict(os.environ if host_environment is None else host_environment)

    def run(
        self,
        request: DockerRunRequest,
        *,
        heartbeat: Callable[[], None],
        cancel_requested: Callable[[], bool],
    ) -> DockerRunResult:
        argv = self._argv(request)
        process_environment = {**self._host_environment, **request.environment}
        started_at = datetime.now(UTC)
        monotonic_deadline = time.monotonic() + request.timeout_seconds
        cancelled = False
        timed_out = False
        try:
            with request.stdout_path.open("wb") as stdout, request.stderr_path.open("wb") as stderr:
                process = subprocess.Popen(  # noqa: S603 - fixed executable and argv, never a shell
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=process_environment,
                    close_fds=True,
                )
                try:
                    while process.poll() is None:
                        heartbeat()
                        if cancel_requested():
                            cancelled = True
                            self._stop(request.container_name)
                            break
                        if time.monotonic() >= monotonic_deadline:
                            timed_out = True
                            self._stop(request.container_name)
                            break
                        time.sleep(self._poll_seconds)
                except BaseException:
                    if process.poll() is None:
                        self._stop(request.container_name)
                    self._wait_or_kill(process, request.container_name)
                    raise
                exit_code = self._wait_or_kill(process, request.container_name)
        except FileNotFoundError as exc:
            raise ExecutorError(
                f"Docker executable {self._docker_binary!r} was not found",
                retryable=False,
            ) from exc
        except OSError as exc:
            raise ExecutorError(f"Docker execution failed: {exc}", retryable=True) from exc
        return DockerRunResult(
            exit_code=exit_code,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            cancelled=cancelled,
            timed_out=timed_out,
        )

    def _argv(self, request: DockerRunRequest) -> list[str]:
        network = "none" if request.network_policy is NetworkPolicy.DENY else "bridge"
        tmpfs_mib = max(16, min(512, request.memory_mib // 4))
        argv = [
            self._docker_binary,
            "run",
            "--rm",
            "--name",
            request.container_name,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "--user",
            "65532:65532",
            "--network",
            network,
            "--cpus",
            f"{request.cpu_millicores / 1000:.3f}",
            "--memory",
            f"{request.memory_mib}m",
            "--mount",
            f"type=bind,src={request.input_directory},dst=/inputs,readonly",
            "--mount",
            f"type=bind,src={request.output_directory},dst=/outputs",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={tmpfs_mib}m",
        ]
        for name in sorted(request.environment):
            argv.extend(("--env", name))
        argv.extend((request.image, *request.command, *request.arguments))
        return argv

    def _stop(self, container_name: str) -> None:
        try:
            subprocess.run(  # noqa: S603 - fixed executable and argv, never a shell
                [
                    self._docker_binary,
                    "stop",
                    "--time",
                    str(self._stop_grace_seconds),
                    container_name,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._host_environment,
                check=False,
                timeout=self._stop_grace_seconds + 5,
            )
        except (OSError, subprocess.TimeoutExpired):
            self._kill(container_name)

    def _kill(self, container_name: str) -> None:
        try:
            subprocess.run(  # noqa: S603 - fixed executable and argv, never a shell
                [self._docker_binary, "kill", container_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._host_environment,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return

    def _wait_or_kill(self, process: subprocess.Popen[bytes], container_name: str) -> int:
        try:
            return process.wait(timeout=self._stop_grace_seconds + 5)
        except subprocess.TimeoutExpired:
            self._kill(container_name)
            try:
                return process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                return process.wait()


class LocalDockerExecutor:
    """Materialize verified inputs, run a restricted container, and validate outputs."""

    name = "local-docker"

    def __init__(
        self,
        artifact_reader: ArtifactReader,
        *,
        runner: DockerRunner | None = None,
        environment: Mapping[str, str] | None = None,
        workspace_root: Path | None = None,
        max_log_bytes: int = 262_144,
        max_output_bytes: int = 1_073_741_824,
    ) -> None:
        self._artifact_reader = artifact_reader
        self._runner = runner or SubprocessDockerRunner()
        self._environment = dict(environment or {})
        self._workspace_root = workspace_root
        self._max_log_bytes = max_log_bytes
        self._max_output_bytes = max_output_bytes

    def execute(
        self,
        lease: JobLease,
        *,
        heartbeat: Callable[[], None],
        cancel_requested: Callable[[], bool],
    ) -> ExecutionOutcome:
        if lease.specification.resources.gpu_count:
            raise ExecutorError(
                "LocalDockerExecutor does not support GPU requests", retryable=False
            )
        root = Path(
            tempfile.mkdtemp(
                prefix=f"hiveblot-job-{lease.attempt_id.hex}-",
                dir=self._workspace_root,
            )
        )
        try:
            input_directory = root / "inputs"
            output_directory = root / "outputs"
            input_directory.mkdir(mode=0o700)
            output_directory.mkdir(mode=0o777)
            output_directory.chmod(0o777)
            self._materialize_inputs(lease, input_directory)
            request = DockerRunRequest(
                container_name=f"hiveblot-job-{lease.attempt_id.hex}",
                image=lease.specification.container.image,
                command=tuple(lease.specification.container.command),
                arguments=lease.specification.container.arguments,
                input_directory=input_directory,
                output_directory=output_directory,
                environment=self._allowed_environment(lease),
                cpu_millicores=lease.specification.resources.cpu_millicores,
                memory_mib=lease.specification.resources.memory_mib,
                network_policy=lease.specification.container.network_policy,
                timeout_seconds=lease.specification.timeout_seconds,
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
            )
            result = self._runner.run(
                request,
                heartbeat=heartbeat,
                cancel_requested=cancel_requested,
            )
            stdout, stdout_truncated = _read_limited(request.stdout_path, self._max_log_bytes)
            stderr, stderr_truncated = _read_limited(request.stderr_path, self._max_log_bytes)
            outputs = (
                self._read_outputs(lease, output_directory)
                if result.exit_code == 0 and not result.cancelled and not result.timed_out
                else ()
            )
            return ExecutionOutcome(
                exit_code=result.exit_code,
                started_at=result.started_at,
                completed_at=result.completed_at,
                cancelled=result.cancelled,
                timed_out=result.timed_out,
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                outputs=outputs,
            )
        finally:
            _remove_workspace(root)

    def _materialize_inputs(self, lease: JobLease, directory: Path) -> None:
        for named in lease.specification.inputs:
            content = self._artifact_reader.read_bytes(named.artifact.artifact_id)
            if len(content) != named.artifact.byte_size:
                raise ExecutorError(
                    "verified artifact reader returned the wrong size", retryable=False
                )
            path = directory / named.name
            path.write_bytes(content)
            path.chmod(0o444)
        directory.chmod(0o555)

    def _allowed_environment(self, lease: JobLease) -> Mapping[str, str]:
        return {
            name: self._environment[name]
            for name in lease.specification.container.allowed_environment_variables
            if name in self._environment
        }

    def _read_outputs(self, lease: JobLease, directory: Path) -> tuple[ExecutionOutput, ...]:
        expected = {item.name: item for item in lease.specification.expected_outputs}
        present_names = {path.name for path in directory.iterdir()}
        unexpected = present_names - expected.keys()
        if unexpected:
            raise InvalidExecutionOutput("container created an undeclared output")
        outputs: list[ExecutionOutput] = []
        total_bytes = 0
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
            outputs.append(
                ExecutionOutput(
                    name=name,
                    media_type=declaration.media_type,
                    content=path.read_bytes(),
                )
            )
        return tuple(outputs)


def _read_limited(path: Path, limit: int) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    size = path.stat().st_size
    with path.open("rb") as handle:
        content = handle.read(limit)
    return content.decode("utf-8", errors="replace"), size > limit


def _remove_workspace(root: Path) -> None:
    for directory, child_directories, _files in os.walk(root, topdown=False):
        for child in child_directories:
            path = Path(directory) / child
            if not path.is_symlink():
                path.chmod(0o700)
        Path(directory).chmod(0o700)
    shutil.rmtree(root, ignore_errors=True)
