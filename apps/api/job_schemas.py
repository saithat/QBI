"""HTTP-only adapters for canonical generic job contracts."""

from __future__ import annotations

import json
from typing import Annotated

from hiveblot_contracts import ContractModel, JobRecord, JobSpecification
from pydantic import BeforeValidator, Field, TypeAdapter

_SPECIFICATION_ADAPTER = TypeAdapter(JobSpecification)


def _json_specification(value: object) -> JobSpecification:
    if isinstance(value, JobSpecification):
        return value
    return _SPECIFICATION_ADAPTER.validate_json(json.dumps(value), strict=True)


type JsonJobSpecification = Annotated[
    JobSpecification,
    BeforeValidator(_json_specification),
]


class SubmitJobRequest(ContractModel):
    specification: JsonJobSpecification


class CancelJobRequest(ContractModel):
    reason: str = Field(min_length=1, max_length=4000)


class JobListResponse(ContractModel):
    count: int = Field(ge=0)
    results: tuple[JobRecord, ...]
