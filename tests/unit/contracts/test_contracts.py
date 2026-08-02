from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hiveblot_contracts import (
    ArtifactReference,
    BoundingRegion,
    ContainerSpecification,
    JobResult,
    JobSpecification,
    PipelineIdentifier,
    ResourceRequirements,
    SucceededJobResult,
)
from pydantic import TypeAdapter, ValidationError

ARTIFACT_ID = UUID("42f433d8-92de-4f2f-896f-3a9ca2286e2f")
JOB_ID = UUID("cb8a5724-500b-476f-8f61-f7b4559cc426")
TRACE_ID = UUID("7fb8c01f-bf7b-4d5f-a2cb-125f12e4be22")
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def artifact() -> ArtifactReference:
    return ArtifactReference(
        artifact_id=ARTIFACT_ID,
        sha256="a" * 64,
        media_type="application/pdf",
        byte_size=128,
    )


def job_specification() -> JobSpecification:
    return JobSpecification(
        job_id=JOB_ID,
        job_type="baseline-normalization",
        idempotency_key="baseline-001",
        pipeline=PipelineIdentifier(name="hackathon-extraction", version="0.1.0"),
        container=ContainerSpecification(image="hiveblot:test", command=("normalize",)),
        resources=ResourceRequirements(cpu_millicores=500, memory_mib=512),
        trace_id=TRACE_ID,
        submitted_at=NOW,
        timeout_seconds=60,
    )


def test_canonical_contracts_are_strict_frozen_and_forbid_unknown_fields() -> None:
    value = artifact()

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ArtifactReference.model_validate(
            {
                **value.model_dump(),
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        ArtifactReference.model_validate({**value.model_dump(), "byte_size": "128"})
    with pytest.raises(ValidationError, match="frozen"):
        value.byte_size = 256


def test_bounding_regions_must_fit_source_canvas() -> None:
    with pytest.raises(ValidationError, match="canvas width"):
        BoundingRegion(
            region_id=UUID("69973318-8967-42d8-8c48-d35b0a7b2047"),
            source_artifact_id=ARTIFACT_ID,
            x=90.0,
            y=0.0,
            width=20.0,
            height=10.0,
            canvas_width=100,
            canvas_height=100,
        )


def test_job_results_use_a_discriminated_terminal_status() -> None:
    result = SucceededJobResult(
        job_id=JOB_ID,
        attempt=1,
        trace_id=TRACE_ID,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=2),
    )
    adapter = TypeAdapter(JobResult)

    assert adapter.validate_json(result.model_dump_json()).status == "succeeded"
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        adapter.validate_python({**result.model_dump(), "status": "running"})


def test_job_specification_round_trips_as_strict_json() -> None:
    specification = job_specification()

    restored = JobSpecification.model_validate_json(specification.model_dump_json())

    assert restored == specification
    assert restored.schema_version == "1.0"
