from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ArtifactReference,
    ContainerSpecification,
    ExpectedJobOutput,
    JobSpecification,
    NamedArtifactReference,
    PipelineIdentifier,
    ResourceRequirements,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def test_job_specification_rejects_unknown_fields_and_undeclared_provenance() -> None:
    with pytest.raises(ValidationError, match="provenance input"):
        _specification(inputs=())

    valid = _specification()
    with pytest.raises(ValidationError, match="Extra inputs"):
        JobSpecification.model_validate_json(
            valid.model_dump_json(exclude_none=False)[:-1] + ',"future_field":true}'
        )


def test_job_specification_rejects_duplicate_names_and_environment_allowlist() -> None:
    source = _artifact()
    with pytest.raises(ValidationError, match="input names must be unique"):
        _specification(
            inputs=(
                NamedArtifactReference(name="input", artifact=source),
                NamedArtifactReference(name="input", artifact=source),
            )
        )
    with pytest.raises(ValidationError, match="environment-variable names must be unique"):
        ContainerSpecification(
            image="hiveblot:test",
            command=("run",),
            allowed_environment_variables=("TOKEN", "TOKEN"),
        )


def _artifact() -> ArtifactReference:
    return ArtifactReference(
        artifact_id=uuid4(),
        sha256="a" * 64,
        media_type="application/json",
        byte_size=10,
    )


def _specification(
    *,
    inputs: tuple[NamedArtifactReference, ...] | None = None,
) -> JobSpecification:
    source = _artifact()
    return JobSpecification(
        job_id=uuid4(),
        job_type="test-operation",
        idempotency_key="case-1",
        pipeline=PipelineIdentifier(name="test", version="1"),
        container=ContainerSpecification(image="hiveblot:test", command=("run",)),
        resources=ResourceRequirements(cpu_millicores=100, memory_mib=128),
        inputs=(NamedArtifactReference(name="input", artifact=source),)
        if inputs is None
        else inputs,
        expected_outputs=(ExpectedJobOutput(name="result", media_type="application/json"),),
        trace_id=uuid4(),
        submitted_at=NOW,
        timeout_seconds=60,
    )
