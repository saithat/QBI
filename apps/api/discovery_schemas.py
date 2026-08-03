"""HTTP-only adapters for public discovery and frontier mutations."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from hiveblot_contracts import ContractModel, DiscoveryBatch
from pydantic import AwareDatetime, BeforeValidator, Field, TypeAdapter

from .evaluation_schemas import JsonUUID

_DISCOVERY_BATCH_ADAPTER = TypeAdapter(DiscoveryBatch)


def _json_datetime(value: object) -> object:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _json_discovery_batch(value: object) -> DiscoveryBatch:
    if isinstance(value, DiscoveryBatch):
        return value
    return _DISCOVERY_BATCH_ADAPTER.validate_json(json.dumps(value), strict=True)


type JsonDiscoveryBatch = Annotated[
    DiscoveryBatch,
    BeforeValidator(_json_discovery_batch),
]
type JsonDatetime = Annotated[AwareDatetime, BeforeValidator(_json_datetime)]


class IngestDiscoveryBatchRequest(ContractModel):
    batch: JsonDiscoveryBatch


class UpdateFrontierScheduleRequest(ContractModel):
    expected_version: int = Field(ge=1)
    priority: int = Field(ge=0, le=1000)
    next_eligible_fetch_at: JsonDatetime


class RetryFrontierRequest(ContractModel):
    expected_version: int = Field(ge=1)
    next_eligible_fetch_at: JsonDatetime
    rationale: str = Field(min_length=1, max_length=4000)


class RecordFrontierAcquisitionRequest(ContractModel):
    expected_version: int = Field(ge=1)
    artifact_id: JsonUUID
    trace_id: JsonUUID


class DiscoveryRunSummary(ContractModel):
    batch_id: UUID
    created_count: int = Field(ge=0)
    deduplicated_count: int = Field(ge=0)
    next_cursor: str | None = None
