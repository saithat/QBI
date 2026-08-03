"""HTTP-only schemas for pipeline definitions, invocations, replay, and publication."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from hiveblot_contracts import ContractModel
from pydantic import Field, model_validator

from .evaluation_schemas import (
    BoundingRegionInput,
    BoundingRegionResponse,
    JsonTuple,
    JsonUUID,
    ValidationIssueInput,
    ValidationIssueResponse,
)


class OutputSchemaInput(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)


class PipelineIdentityInput(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)


class _ComponentInputBase(ContractModel):
    component_key: str = Field(min_length=1, max_length=200)
    depends_on: JsonTuple[str] = ()
    output_schema: OutputSchemaInput
    configuration_json: str = Field(default="{}", min_length=2)


class ModelComponentInput(_ComponentInputBase):
    component_type: Literal["model"] = "model"
    provider: str = Field(min_length=1, max_length=200)
    model_name: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=200)


class ToolComponentInput(_ComponentInputBase):
    component_type: Literal["tool"] = "tool"
    tool_name: str = Field(min_length=1, max_length=200)
    tool_version: str = Field(min_length=1, max_length=200)


type PipelineComponentInput = Annotated[
    ModelComponentInput | ToolComponentInput,
    Field(discriminator="component_type"),
]


class CreatePipelineDefinitionRequest(ContractModel):
    pipeline: PipelineIdentityInput
    description: str | None = Field(default=None, max_length=4000)
    components: JsonTuple[PipelineComponentInput] = Field(min_length=1)
    configuration_json: str = Field(default="{}", min_length=2)


class CreatePipelineRunRequest(ContractModel):
    definition_id: JsonUUID
    input_artifact_ids: JsonTuple[JsonUUID] = Field(min_length=1)
    visibility: Literal["public", "organization_private"] | None = None
    organization_id: JsonUUID | None = None
    configuration_json: str = Field(default="{}", min_length=2)
    trace_id: JsonUUID

    @model_validator(mode="after")
    def explicit_scope_is_complete(self) -> Self:
        if self.visibility == "public" and self.organization_id is not None:
            raise ValueError("public pipeline runs cannot include organization_id")
        if self.visibility == "organization_private" and self.organization_id is None:
            raise ValueError("organization-private pipeline runs require organization_id")
        if self.visibility is None and self.organization_id is not None:
            raise ValueError("organization_id requires an explicit visibility")
        return self


class ComponentEvidenceInput(ContractModel):
    artifact_id: JsonUUID
    field_path: str | None = Field(default=None, min_length=1, max_length=1000)
    region: BoundingRegionInput | None = None
    description: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def evidence_is_located_or_described(self) -> Self:
        if self.field_path is None and self.region is None and self.description is None:
            raise ValueError("component evidence must include a field, region, or description")
        if self.region is not None and self.region.source_artifact_id != self.artifact_id:
            raise ValueError("component evidence region must use the referenced artifact")
        return self


class _ComponentResultRequestBase(ContractModel):
    output_artifact_ids: JsonTuple[JsonUUID] = ()
    evidence: JsonTuple[ComponentEvidenceInput] = ()
    validation_issues: JsonTuple[ValidationIssueInput] = ()
    latency_ms: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)


class SuccessfulComponentResultRequest(_ComponentResultRequestBase):
    outcome: Literal["succeeded"] = "succeeded"
    output_schema: OutputSchemaInput
    raw_output_json: str = Field(min_length=1)
    normalized_output_json: str = Field(min_length=1)


class FailedComponentResultRequest(_ComponentResultRequestBase):
    outcome: Literal["failed"] = "failed"
    failure_kind: Literal["execution", "cancelled"]
    error_code: str = Field(min_length=1, max_length=200)
    error_message: str = Field(min_length=1, max_length=8000)
    raw_output_json: str | None = Field(default=None, min_length=1)
    normalized_output_json: str | None = Field(default=None, min_length=1)


type CompleteComponentResultRequest = Annotated[
    SuccessfulComponentResultRequest | FailedComponentResultRequest,
    Field(discriminator="outcome"),
]


class ReplayComponentRequest(ContractModel):
    configuration_json: str | None = Field(default=None, min_length=2)
    trace_id: JsonUUID


class PublishedValueInput(ContractModel):
    field_path: str = Field(pattern=r"^(?:/(?:[^/~]|~[01])+)*$", max_length=1000)
    producing_invocation_id: JsonUUID
    result_path: str = Field(pattern=r"^(?:/(?:[^/~]|~[01])+)*$", max_length=1000)
    value_json: str = Field(min_length=1)
    evidence: JsonTuple[ComponentEvidenceInput] = ()


class PublishPipelineRunRequest(ContractModel):
    output_schema: OutputSchemaInput
    normalized_output_json: str = Field(min_length=1)
    values: JsonTuple[PublishedValueInput] = Field(min_length=1)
    output_artifact_ids: JsonTuple[JsonUUID] = ()
    trace_id: JsonUUID


class OutputSchemaResponse(ContractModel):
    name: str
    version: str


class ArtifactReferenceResponse(ContractModel):
    artifact_id: UUID
    sha256: str
    media_type: str
    byte_size: int


class PipelineComponentResponse(ContractModel):
    component_type: Literal["model", "tool"]
    component_key: str
    depends_on: tuple[str, ...]
    output_schema: OutputSchemaResponse
    configuration_json: str
    producer_name: str
    producer_version: str
    provider: str | None
    prompt_version: str | None


class PipelineDefinitionResponse(ContractModel):
    definition_id: UUID
    pipeline_name: str
    pipeline_version: str
    description: str | None
    components: tuple[PipelineComponentResponse, ...]
    configuration_json: str
    created_at: datetime


class PipelineDefinitionListResponse(ContractModel):
    definitions: tuple[PipelineDefinitionResponse, ...]


class ComponentEvidenceResponse(ContractModel):
    artifact: ArtifactReferenceResponse
    field_path: str | None
    region: BoundingRegionResponse | None
    description: str | None


class ComponentResultResponse(ContractModel):
    result_type: Literal["succeeded", "failed"]
    failure_kind: Literal["execution", "output_validation", "cancelled"] | None
    error_code: str | None
    error_message: str | None
    output_schema: OutputSchemaResponse
    raw_output_json: str | None
    normalized_output_json: str | None
    output_artifacts: tuple[ArtifactReferenceResponse, ...]
    evidence: tuple[ComponentEvidenceResponse, ...]
    validation_issues: tuple[ValidationIssueResponse, ...]
    latency_ms: int
    cost_microusd: int
    completed_at: datetime


class ComponentInvocationResponse(ContractModel):
    invocation_id: UUID
    run_id: UUID
    component: PipelineComponentResponse
    status: Literal["pending", "succeeded", "failed"]
    input_artifacts: tuple[ArtifactReferenceResponse, ...]
    parent_invocation_ids: tuple[UUID, ...]
    replay_of_invocation_id: UUID | None
    configuration_json: str
    trace_id: UUID
    result: ComponentResultResponse | None
    created_at: datetime


class PipelineRunResponse(ContractModel):
    run_id: UUID
    case_id: UUID
    definition_id: UUID
    pipeline_name: str
    pipeline_version: str
    status: Literal["active", "published"]
    visibility: Literal["public", "organization_private"]
    organization_id: UUID | None
    input_artifacts: tuple[ArtifactReferenceResponse, ...]
    configuration_json: str
    trace_id: UUID
    created_at: datetime
    updated_at: datetime


class PublishedValueResponse(ContractModel):
    field_path: str
    producing_invocation_id: UUID
    result_path: str
    value_json: str
    evidence: tuple[ComponentEvidenceResponse, ...]


class PipelinePublicationResponse(ContractModel):
    publication_id: UUID
    run_id: UUID
    case_id: UUID
    pipeline_name: str
    pipeline_version: str
    visibility: Literal["public", "organization_private"]
    organization_id: UUID | None
    output_schema: OutputSchemaResponse
    normalized_output_json: str
    values: tuple[PublishedValueResponse, ...]
    output_artifacts: tuple[ArtifactReferenceResponse, ...]
    trace_id: UUID
    created_at: datetime


class PipelineRunDetailResponse(ContractModel):
    run: PipelineRunResponse
    invocations: tuple[ComponentInvocationResponse, ...]
    publications: tuple[PipelinePublicationResponse, ...]


class PipelineRunListResponse(ContractModel):
    case_id: UUID
    runs: tuple[PipelineRunDetailResponse, ...]
