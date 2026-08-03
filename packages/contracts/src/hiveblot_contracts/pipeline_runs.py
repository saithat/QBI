"""Versioned pipeline definitions, invocations, results, replay, and publication."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .artifacts import ArtifactReference, ArtifactVisibility, BoundingRegion
from .base import ContractModel, Identifier
from .evaluation import ValidationIssue
from .identifiers import ModelIdentifier, PipelineIdentifier, ToolIdentifier


class OutputSchemaIdentifier(ContractModel):
    name: Identifier
    version: Identifier


class _PipelineComponentBase(ContractModel):
    component_key: Identifier
    depends_on: tuple[Identifier, ...] = ()
    output_schema: OutputSchemaIdentifier
    configuration_json: str = Field(default="{}", min_length=2)

    @model_validator(mode="after")
    def component_configuration_is_an_object(self) -> Self:
        _validate_json_object(self.configuration_json, "component configuration_json")
        if self.component_key in self.depends_on:
            raise ValueError("pipeline components cannot depend on themselves")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("component dependencies must be unique")
        return self


class ModelPipelineComponent(_PipelineComponentBase):
    component_type: Literal["model"] = "model"
    model: ModelIdentifier
    prompt_version: Identifier


class ToolPipelineComponent(_PipelineComponentBase):
    component_type: Literal["tool"] = "tool"
    tool: ToolIdentifier


type PipelineComponentDefinition = Annotated[
    ModelPipelineComponent | ToolPipelineComponent,
    Field(discriminator="component_type"),
]


class PipelineDefinitionRecord(ContractModel):
    definition_id: UUID
    pipeline: PipelineIdentifier
    description: str | None = Field(default=None, max_length=4000)
    components: tuple[PipelineComponentDefinition, ...] = Field(min_length=1)
    configuration_json: str = Field(default="{}", min_length=2)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def component_graph_is_valid(self) -> Self:
        _validate_json_object(self.configuration_json, "pipeline configuration_json")
        keys = [component.component_key for component in self.components]
        if len(keys) != len(set(keys)):
            raise ValueError("pipeline component keys must be unique")
        known = set(keys)
        for component in self.components:
            unknown = set(component.depends_on) - known
            if unknown:
                raise ValueError("component dependencies must reference the pipeline definition")
        _validate_component_dag(self.components)
        return self


class PipelineRunStatus(StrEnum):
    ACTIVE = "active"
    PUBLISHED = "published"


class PipelineRunRecord(ContractModel):
    run_id: UUID
    case_id: UUID
    definition_id: UUID
    pipeline: PipelineIdentifier
    status: PipelineRunStatus
    visibility: ArtifactVisibility = ArtifactVisibility.PUBLIC
    organization_id: UUID | None = None
    input_artifacts: tuple[ArtifactReference, ...] = Field(min_length=1)
    configuration_json: str = Field(default="{}", min_length=2)
    trace_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def run_is_valid(self) -> Self:
        _validate_json_object(self.configuration_json, "run configuration_json")
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public pipeline runs cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private pipeline runs require an organization")
        artifact_ids = [artifact.artifact_id for artifact in self.input_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("pipeline run input artifacts must be unique")
        if self.updated_at < self.created_at:
            raise ValueError("pipeline run updated_at cannot precede created_at")
        return self


class ComponentInvocationStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ComponentEvidenceReference(ContractModel):
    artifact: ArtifactReference
    field_path: str | None = Field(default=None, min_length=1, max_length=1000)
    region: BoundingRegion | None = None
    description: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def evidence_is_located_or_described(self) -> Self:
        if self.field_path is None and self.region is None and self.description is None:
            raise ValueError("component evidence must include a field, region, or description")
        if self.region is not None and self.region.source_artifact_id != self.artifact.artifact_id:
            raise ValueError("component evidence region must use the referenced artifact")
        return self


class InvocationFailureKind(StrEnum):
    EXECUTION = "execution"
    OUTPUT_VALIDATION = "output_validation"
    CANCELLED = "cancelled"


class SucceededComponentResult(ContractModel):
    result_type: Literal["succeeded"] = "succeeded"
    output_schema: OutputSchemaIdentifier
    raw_output_json: str = Field(min_length=1)
    normalized_output_json: str = Field(min_length=1)
    output_artifacts: tuple[ArtifactReference, ...] = ()
    evidence: tuple[ComponentEvidenceReference, ...] = ()
    validation_issues: tuple[ValidationIssue, ...] = ()
    latency_ms: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def successful_result_is_valid(self) -> Self:
        _validate_json_document(self.normalized_output_json, "normalized_output_json")
        _validate_output_artifacts(self.output_artifacts)
        return self


class FailedComponentResult(ContractModel):
    result_type: Literal["failed"] = "failed"
    failure_kind: InvocationFailureKind
    output_schema: OutputSchemaIdentifier
    error_code: Identifier
    error_message: str = Field(min_length=1, max_length=8000)
    raw_output_json: str | None = Field(default=None, min_length=1)
    normalized_output_json: str | None = Field(default=None, min_length=1)
    output_artifacts: tuple[ArtifactReference, ...] = ()
    evidence: tuple[ComponentEvidenceReference, ...] = ()
    validation_issues: tuple[ValidationIssue, ...] = ()
    latency_ms: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def failed_result_artifacts_are_unique(self) -> Self:
        _validate_output_artifacts(self.output_artifacts)
        return self


type ComponentResult = Annotated[
    SucceededComponentResult | FailedComponentResult,
    Field(discriminator="result_type"),
]


class ComponentInvocationRecord(ContractModel):
    invocation_id: UUID
    run_id: UUID
    component: PipelineComponentDefinition
    status: ComponentInvocationStatus
    input_artifacts: tuple[ArtifactReference, ...]
    parent_invocation_ids: tuple[UUID, ...] = ()
    replay_of_invocation_id: UUID | None = None
    configuration_json: str = Field(default="{}", min_length=2)
    trace_id: UUID
    result: ComponentResult | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def invocation_state_is_consistent(self) -> Self:
        _validate_json_object(self.configuration_json, "invocation configuration_json")
        if len(self.parent_invocation_ids) != len(set(self.parent_invocation_ids)):
            raise ValueError("parent invocation IDs must be unique")
        if self.invocation_id in self.parent_invocation_ids:
            raise ValueError("an invocation cannot be its own parent")
        if self.replay_of_invocation_id == self.invocation_id:
            raise ValueError("an invocation cannot replay itself")
        expected = {
            ComponentInvocationStatus.PENDING: None,
            ComponentInvocationStatus.SUCCEEDED: "succeeded",
            ComponentInvocationStatus.FAILED: "failed",
        }[self.status]
        actual = self.result.result_type if self.result is not None else None
        if actual != expected:
            raise ValueError("component invocation status and result must agree")
        artifact_ids = [artifact.artifact_id for artifact in self.input_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("component invocation input artifacts must be unique")
        if self.result is not None and self.result.completed_at < self.created_at:
            raise ValueError("component result cannot complete before invocation creation")
        return self


class PublishedPipelineValue(ContractModel):
    field_path: str = Field(pattern=r"^(?:/(?:[^/~]|~[01])+)*$", max_length=1000)
    producing_invocation_id: UUID
    result_path: str = Field(pattern=r"^(?:/(?:[^/~]|~[01])+)*$", max_length=1000)
    value_json: str = Field(min_length=1)
    evidence: tuple[ComponentEvidenceReference, ...] = ()

    @model_validator(mode="after")
    def published_value_is_json(self) -> Self:
        _validate_json_document(self.value_json, "published value_json")
        return self


class PipelinePublicationRecord(ContractModel):
    publication_id: UUID
    run_id: UUID
    case_id: UUID
    pipeline: PipelineIdentifier
    visibility: ArtifactVisibility = ArtifactVisibility.PUBLIC
    organization_id: UUID | None = None
    output_schema: OutputSchemaIdentifier
    normalized_output_json: str = Field(min_length=1)
    values: tuple[PublishedPipelineValue, ...] = Field(min_length=1)
    output_artifacts: tuple[ArtifactReference, ...] = ()
    trace_id: UUID
    created_at: AwareDatetime

    @model_validator(mode="after")
    def publication_is_valid(self) -> Self:
        _validate_json_document(self.normalized_output_json, "publication normalized_output_json")
        if self.visibility is ArtifactVisibility.PUBLIC and self.organization_id is not None:
            raise ValueError("public pipeline publications cannot belong to an organization")
        if (
            self.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
            and self.organization_id is None
        ):
            raise ValueError("organization-private pipeline publications require an organization")
        paths = [value.field_path for value in self.values]
        if len(paths) != len(set(paths)):
            raise ValueError("published field paths must be unique")
        artifact_ids = [artifact.artifact_id for artifact in self.output_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("publication output artifacts must be unique")
        return self


class PipelineRunDetail(ContractModel):
    run: PipelineRunRecord
    invocations: tuple[ComponentInvocationRecord, ...]
    publications: tuple[PipelinePublicationRecord, ...]

    @model_validator(mode="after")
    def children_belong_to_run(self) -> Self:
        if any(invocation.run_id != self.run.run_id for invocation in self.invocations):
            raise ValueError("component invocations must belong to the pipeline run")
        if any(
            publication.run_id != self.run.run_id
            or publication.case_id != self.run.case_id
            or publication.pipeline != self.run.pipeline
            or publication.visibility is not self.run.visibility
            or publication.organization_id != self.run.organization_id
            for publication in self.publications
        ):
            raise ValueError("pipeline publications must belong to and match the pipeline run")
        invocation_id_list = [item.invocation_id for item in self.invocations]
        if len(invocation_id_list) != len(set(invocation_id_list)):
            raise ValueError("pipeline run invocation IDs must be unique")
        publication_ids = [item.publication_id for item in self.publications]
        if len(publication_ids) != len(set(publication_ids)):
            raise ValueError("pipeline run publication IDs must be unique")
        invocation_ids = {invocation.invocation_id for invocation in self.invocations}
        if any(
            parent_id not in invocation_ids
            for invocation in self.invocations
            for parent_id in invocation.parent_invocation_ids
        ):
            raise ValueError("invocation parents must belong to the pipeline run detail")
        invocations_by_id = {item.invocation_id: item for item in self.invocations}
        for invocation in self.invocations:
            replay_id = invocation.replay_of_invocation_id
            if replay_id is None:
                continue
            replayed = invocations_by_id.get(replay_id)
            if replayed is None:
                raise ValueError("replay sources must belong to the pipeline run detail")
            if replayed.component.component_key != invocation.component.component_key:
                raise ValueError("replays must retain the original component identity")
        if any(
            value.producing_invocation_id not in invocation_ids
            for publication in self.publications
            for value in publication.values
        ):
            raise ValueError("published values must name invocations in the pipeline run detail")
        return self


def _validate_component_dag(components: tuple[PipelineComponentDefinition, ...]) -> None:
    dependencies = {component.component_key: component.depends_on for component in components}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_key: str) -> None:
        if component_key in visiting:
            raise ValueError("pipeline component dependencies cannot contain a cycle")
        if component_key in visited:
            return
        visiting.add(component_key)
        for parent in dependencies[component_key]:
            visit(parent)
        visiting.remove(component_key)
        visited.add(component_key)

    for component_key in dependencies:
        visit(component_key)


def _validate_json_object(value: str, label: str) -> None:
    parsed = _parse_json(value, label)
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must contain a JSON object")


def _validate_json_document(value: str, label: str) -> None:
    _parse_json(value, label)


def _parse_json(value: str, label: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must contain valid JSON") from exc


def _validate_output_artifacts(output_artifacts: tuple[ArtifactReference, ...]) -> None:
    output_ids = [artifact.artifact_id for artifact in output_artifacts]
    if len(output_ids) != len(set(output_ids)):
        raise ValueError("component output artifacts must be unique")
