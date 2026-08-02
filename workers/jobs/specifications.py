"""Domain job factories kept outside the generic execution service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactReference,
    ContainerSpecification,
    ExpectedJobOutput,
    JobSpecification,
    NamedArtifactReference,
    PipelineIdentifier,
    ResourceRequirements,
)


def legacy_normalization_job(
    source: ArtifactReference,
    *,
    image: str,
    idempotency_key: str,
    trace_id: UUID | None = None,
    job_id: UUID | None = None,
    submitted_at: datetime | None = None,
) -> JobSpecification:
    """Describe the preserved legacy normalizer as one portable finite job."""

    return JobSpecification(
        job_id=job_id or uuid4(),
        job_type="western-blot-legacy-normalization",
        idempotency_key=idempotency_key,
        pipeline=PipelineIdentifier(name="hackathon-normalization", version="1.0.0"),
        container=ContainerSpecification(
            image=image,
            command=("hiveblot-job-operation",),
            arguments=(
                "western-blot-normalize",
                "--input",
                "/inputs/model_output",
                "--output",
                "/outputs/records",
            ),
        ),
        resources=ResourceRequirements(cpu_millicores=500, memory_mib=512),
        inputs=(NamedArtifactReference(name="model_output", artifact=source),),
        expected_outputs=(ExpectedJobOutput(name="records", media_type="application/json"),),
        trace_id=trace_id or uuid4(),
        submitted_at=submitted_at or datetime.now(UTC),
        timeout_seconds=120,
        max_attempts=3,
    )
