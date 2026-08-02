"""Queue decoding helpers proving the shared job contracts are worker-safe."""

from __future__ import annotations

from hiveblot_contracts import JobResult, JobSpecification
from pydantic import TypeAdapter

JOB_RESULT_ADAPTER: TypeAdapter[JobResult] = TypeAdapter(JobResult)


def decode_job_specification(payload: bytes | str) -> JobSpecification:
    return JobSpecification.model_validate_json(payload)


def decode_job_result(payload: bytes | str) -> JobResult:
    return JOB_RESULT_ADAPTER.validate_json(payload)
