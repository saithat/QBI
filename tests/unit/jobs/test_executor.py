from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from hiveblot_contracts import ArtifactReference, ArtifactVisibility
from hiveblot_job_service import (
    DockerRunRequest,
    DockerRunResult,
    InvalidExecutionOutput,
    LocalDockerExecutor,
    SubprocessDockerRunner,
)
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    SourceAdapterRegistry,
    VerifiedArtifactReader,
)

from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore
from workers.jobs.specifications import legacy_normalization_job

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


class RecordingRunner:
    def __init__(
        self,
        *,
        unexpected_output: bool = False,
        expected_environment: dict[str, str] | None = None,
    ) -> None:
        self.request: DockerRunRequest | None = None
        self.unexpected_output = unexpected_output
        self.expected_environment = expected_environment or {}

    def run(self, request, *, heartbeat, cancel_requested):
        self.request = request
        assert (request.input_directory / "model_output").read_bytes() == b'{"input":true}'
        assert request.environment == self.expected_environment
        heartbeat()
        assert not cancel_requested()
        request.stdout_path.write_text("normalized 40 records\n", encoding="utf-8")
        (request.output_directory / "records").write_bytes(b'[{"target":"TP53"}]\n')
        if self.unexpected_output:
            (request.output_directory / "undeclared").write_text("no", encoding="utf-8")
        return DockerRunResult(
            exit_code=0,
            started_at=NOW,
            completed_at=NOW,
            cancelled=False,
            timed_out=False,
        )


def test_local_docker_executor_materializes_read_only_inputs_and_validates_outputs(
    tmp_path: Path,
) -> None:
    reader, reference = _reader_fixture()
    runner = RecordingRunner(expected_environment={"SAFE_SETTING": "visible"})
    specification = legacy_normalization_job(
        reference,
        image="hiveblot:test",
        idempotency_key="executor-case",
        submitted_at=NOW,
    )
    specification = specification.model_copy(
        update={
            "container": specification.container.model_copy(
                update={"allowed_environment_variables": ("SAFE_SETTING",)}
            )
        }
    )
    from uuid import uuid4

    from hiveblot_contracts import JobLease

    lease = JobLease(
        lease_token=uuid4(),
        job_id=specification.job_id,
        attempt_id=uuid4(),
        attempt=1,
        worker_id="worker-a",
        specification=specification,
        leased_at=NOW,
        expires_at=NOW.replace(minute=1),
    )
    executor = LocalDockerExecutor(
        reader,
        runner=runner,
        environment={"SAFE_SETTING": "visible", "SECRET": "hidden"},
        workspace_root=tmp_path,
    )

    outcome = executor.execute(lease, heartbeat=lambda: None, cancel_requested=lambda: False)

    assert outcome.outputs[0].content == b'[{"target":"TP53"}]\n'
    assert outcome.stdout == "normalized 40 records\n"
    assert runner.request is not None
    assert runner.request.network_policy.value == "deny"
    assert runner.request.cpu_millicores == 500
    argv = SubprocessDockerRunner(host_environment={})._argv(runner.request)
    assert "--read-only" in argv
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert "SAFE_SETTING" in argv
    assert "visible" not in argv
    assert "SECRET" not in argv
    assert not any(tmp_path.iterdir())


def test_local_docker_executor_rejects_undeclared_output(tmp_path: Path) -> None:
    reader, reference = _reader_fixture()
    runner = RecordingRunner(unexpected_output=True)
    specification = legacy_normalization_job(
        reference,
        image="hiveblot:test",
        idempotency_key="invalid-output",
        submitted_at=NOW,
    )
    from uuid import uuid4

    from hiveblot_contracts import JobLease

    lease = JobLease(
        lease_token=uuid4(),
        job_id=specification.job_id,
        attempt_id=uuid4(),
        attempt=1,
        worker_id="worker-a",
        specification=specification,
        leased_at=NOW,
        expires_at=NOW.replace(minute=1),
    )
    executor = LocalDockerExecutor(
        reader,
        runner=runner,
        environment={"SAFE_SETTING": "visible"},
        workspace_root=tmp_path,
    )

    with pytest.raises(InvalidExecutionOutput, match="undeclared"):
        executor.execute(lease, heartbeat=lambda: None, cancel_requested=lambda: False)


def _reader_fixture() -> tuple[VerifiedArtifactReader, ArtifactReference]:
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
    content = b'{"input":true}'
    upload = service.begin_multipart_upload(
        original_filename="input.json",
        declared_media_type="application/json",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:test:executor-input",
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
    return (
        VerifiedArtifactReader(service, repository, store),
        ArtifactReference(
            artifact_id=artifact.artifact_id,
            sha256=artifact.sha256,
            media_type=artifact.media_type,
            byte_size=artifact.byte_size,
        ),
    )
