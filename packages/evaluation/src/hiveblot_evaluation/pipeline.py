"""Immutable pipeline-run registry, output validation, replay, and publication."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactRecord,
    ArtifactReference,
    BoundingRegion,
    ComponentEvidenceReference,
    ComponentInvocationRecord,
    ComponentInvocationStatus,
    ComponentResult,
    FailedComponentResult,
    InvocationFailureKind,
    OutputSchemaIdentifier,
    PipelineComponentDefinition,
    PipelineDefinitionRecord,
    PipelineIdentifier,
    PipelinePublicationRecord,
    PipelineRunDetail,
    PipelineRunRecord,
    PipelineRunStatus,
    PublishedPipelineValue,
    SucceededComponentResult,
    ValidationIssue,
    ValidationSeverity,
)
from hiveblot_contracts.registry import CONTRACT_REGISTRY
from pydantic import TypeAdapter, ValidationError

from .errors import ConcurrencyConflict, EvaluationNotFound, InvalidEvaluationState
from .pipeline_repository import PipelineRunRepository
from .service import EvaluationService


class PipelineArtifactLookup(Protocol):
    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord: ...


class OutputSchemaRegistry:
    def __init__(self, contracts: Mapping[tuple[str, str], Any] | None = None) -> None:
        configured = (
            contracts
            if contracts is not None
            else {(name, "1.0"): contract for name, contract in CONTRACT_REGISTRY.items()}
        )
        self._adapters = {key: TypeAdapter(contract) for key, contract in configured.items()}

    def contains(self, schema: OutputSchemaIdentifier) -> bool:
        return (schema.name, schema.version) in self._adapters

    def validate_json(self, schema: OutputSchemaIdentifier, value: str) -> None:
        try:
            adapter = self._adapters[(schema.name, schema.version)]
        except KeyError as exc:
            raise InvalidEvaluationState(
                f"output schema {schema.name}@{schema.version} is not registered"
            ) from exc
        adapter.validate_json(value, strict=True)


class PipelineRegistryService:
    def __init__(
        self,
        repository: PipelineRunRepository,
        evaluation: EvaluationService,
        artifacts: PipelineArtifactLookup,
        *,
        schemas: OutputSchemaRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._evaluation = evaluation
        self._artifacts = artifacts
        self._schemas = schemas or OutputSchemaRegistry()
        self._clock = clock or (lambda: datetime.now(UTC))

    def register_definition(
        self,
        *,
        pipeline: PipelineIdentifier,
        description: str | None,
        components: tuple[PipelineComponentDefinition, ...],
        configuration_json: str,
    ) -> PipelineDefinitionRecord:
        definition = PipelineDefinitionRecord(
            definition_id=uuid4(),
            pipeline=pipeline,
            description=description,
            components=components,
            configuration_json=configuration_json,
            created_at=self._clock(),
        )
        unknown = [
            component.output_schema
            for component in definition.components
            if not self._schemas.contains(component.output_schema)
        ]
        if unknown:
            rendered = ", ".join(f"{item.name}@{item.version}" for item in unknown)
            raise InvalidEvaluationState(f"pipeline uses unregistered output schemas: {rendered}")
        return self._repository.create_definition(definition)

    def get_definition(self, definition_id: UUID) -> PipelineDefinitionRecord:
        definition = self._repository.get_definition(definition_id)
        if definition is None:
            raise EvaluationNotFound(f"pipeline definition {definition_id} does not exist")
        return definition

    def get_definition_by_pipeline(
        self,
        pipeline: PipelineIdentifier,
    ) -> PipelineDefinitionRecord:
        definition = self._repository.get_definition_by_pipeline(pipeline)
        if definition is None:
            raise EvaluationNotFound(
                f"pipeline definition {pipeline.name}@{pipeline.version} does not exist"
            )
        return definition

    def list_definitions(self) -> Sequence[PipelineDefinitionRecord]:
        return self._repository.list_definitions()

    def create_run(
        self,
        case_id: UUID,
        *,
        definition_id: UUID,
        input_artifact_ids: tuple[UUID, ...],
        configuration_json: str,
        trace_id: UUID,
    ) -> PipelineRunDetail:
        case = self._evaluation.get_case(case_id)
        definition = self.get_definition(definition_id)
        if not input_artifact_ids:
            raise InvalidEvaluationState("a pipeline run requires at least one input artifact")
        if len(input_artifact_ids) != len(set(input_artifact_ids)):
            raise InvalidEvaluationState("pipeline run input artifact IDs must be unique")
        case_sources = {source.artifact_id for source in case.source_artifacts}
        if not set(input_artifact_ids).issubset(case_sources):
            raise InvalidEvaluationState("pipeline run inputs must be source artifacts of the case")
        inputs = tuple(self._artifact_reference(artifact_id) for artifact_id in input_artifact_ids)
        now = self._clock()
        run = PipelineRunRecord(
            run_id=uuid4(),
            case_id=case_id,
            definition_id=definition.definition_id,
            pipeline=definition.pipeline,
            status=PipelineRunStatus.ACTIVE,
            input_artifacts=inputs,
            configuration_json=configuration_json,
            trace_id=trace_id,
            created_at=now,
            updated_at=now,
        )
        invocation_ids = {component.component_key: uuid4() for component in definition.components}
        invocations = tuple(
            ComponentInvocationRecord(
                invocation_id=invocation_ids[component.component_key],
                run_id=run.run_id,
                component=component,
                status=ComponentInvocationStatus.PENDING,
                input_artifacts=inputs,
                parent_invocation_ids=tuple(
                    invocation_ids[parent_key] for parent_key in component.depends_on
                ),
                configuration_json=component.configuration_json,
                trace_id=trace_id,
                created_at=now,
            )
            for component in definition.components
        )
        self._repository.create_run(run, invocations)
        return PipelineRunDetail(run=run, invocations=invocations, publications=())

    def get_run(self, run_id: UUID) -> PipelineRunRecord:
        run = self._repository.get_run(run_id)
        if run is None:
            raise EvaluationNotFound(f"pipeline run {run_id} does not exist")
        return run

    def get_run_detail(self, run_id: UUID) -> PipelineRunDetail:
        run = self.get_run(run_id)
        return PipelineRunDetail(
            run=run,
            invocations=self._ordered_invocations(run),
            publications=tuple(self._repository.list_publications(run_id)),
        )

    def list_runs(self, case_id: UUID) -> Sequence[PipelineRunDetail]:
        self._evaluation.get_case(case_id)
        return tuple(
            PipelineRunDetail(
                run=run,
                invocations=self._ordered_invocations(run),
                publications=tuple(self._repository.list_publications(run.run_id)),
            )
            for run in self._repository.list_runs(case_id)
        )

    def get_invocation(self, invocation_id: UUID) -> ComponentInvocationRecord:
        invocation = self._repository.get_invocation(invocation_id)
        if invocation is None:
            raise EvaluationNotFound(f"component invocation {invocation_id} does not exist")
        return invocation

    def evidence_reference(
        self,
        *,
        artifact_id: UUID,
        field_path: str | None,
        region: BoundingRegion | None,
        description: str | None,
    ) -> ComponentEvidenceReference:
        return ComponentEvidenceReference(
            artifact=self._artifact_reference(artifact_id),
            field_path=field_path,
            region=region,
            description=description,
        )

    def complete_success(
        self,
        invocation_id: UUID,
        *,
        output_schema: OutputSchemaIdentifier,
        raw_output_json: str,
        normalized_output_json: str,
        output_artifact_ids: tuple[UUID, ...],
        evidence: tuple[ComponentEvidenceReference, ...],
        validation_issues: tuple[ValidationIssue, ...],
        latency_ms: int,
        cost_microusd: int,
    ) -> ComponentInvocationRecord:
        invocation = self._pending_invocation(invocation_id)
        self._validate_parent_results(invocation)
        output_artifacts = tuple(
            self._artifact_reference(artifact_id) for artifact_id in output_artifact_ids
        )
        self._validate_result_references(invocation, output_artifacts, evidence, validation_issues)
        validation_error: str | None = None
        if output_schema != invocation.component.output_schema:
            validation_error = "result output schema does not match the component definition"
        else:
            try:
                self._schemas.validate_json(output_schema, normalized_output_json)
            except (InvalidEvaluationState, ValidationError, ValueError) as exc:
                validation_error = str(exc)
        completed_at = self._clock()
        if validation_error is not None:
            issue = ValidationIssue(
                issue_id=uuid4(),
                severity=ValidationSeverity.ERROR,
                code="output_schema_validation_failed",
                message=validation_error[:4000] or "component output schema validation failed",
                evidence_artifact_ids=tuple(artifact.artifact_id for artifact in output_artifacts),
            )
            result: ComponentResult = FailedComponentResult(
                failure_kind=InvocationFailureKind.OUTPUT_VALIDATION,
                output_schema=output_schema,
                error_code="output_schema_validation_failed",
                error_message=issue.message,
                raw_output_json=raw_output_json,
                normalized_output_json=normalized_output_json,
                output_artifacts=output_artifacts,
                evidence=evidence,
                validation_issues=(*validation_issues, issue),
                latency_ms=latency_ms,
                cost_microusd=cost_microusd,
                completed_at=completed_at,
            )
            status = ComponentInvocationStatus.FAILED
        else:
            result = SucceededComponentResult(
                output_schema=output_schema,
                raw_output_json=raw_output_json,
                normalized_output_json=normalized_output_json,
                output_artifacts=output_artifacts,
                evidence=evidence,
                validation_issues=validation_issues,
                latency_ms=latency_ms,
                cost_microusd=cost_microusd,
                completed_at=completed_at,
            )
            status = ComponentInvocationStatus.SUCCEEDED
        completed = invocation.model_copy(update={"status": status, "result": result})
        return self._repository.complete_invocation(
            ComponentInvocationRecord.model_validate(completed.model_dump(mode="python"))
        )

    def complete_failure(
        self,
        invocation_id: UUID,
        *,
        failure_kind: InvocationFailureKind,
        error_code: str,
        error_message: str,
        raw_output_json: str | None,
        normalized_output_json: str | None,
        output_artifact_ids: tuple[UUID, ...],
        evidence: tuple[ComponentEvidenceReference, ...],
        validation_issues: tuple[ValidationIssue, ...],
        latency_ms: int,
        cost_microusd: int,
    ) -> ComponentInvocationRecord:
        invocation = self._pending_invocation(invocation_id)
        self._validate_parent_results(invocation)
        if failure_kind is InvocationFailureKind.OUTPUT_VALIDATION:
            raise InvalidEvaluationState(
                "output-validation failures are produced by schema validation"
            )
        output_artifacts = tuple(
            self._artifact_reference(artifact_id) for artifact_id in output_artifact_ids
        )
        self._validate_result_references(invocation, output_artifacts, evidence, validation_issues)
        result = FailedComponentResult(
            failure_kind=failure_kind,
            output_schema=invocation.component.output_schema,
            error_code=error_code,
            error_message=error_message,
            raw_output_json=raw_output_json,
            normalized_output_json=normalized_output_json,
            output_artifacts=output_artifacts,
            evidence=evidence,
            validation_issues=validation_issues,
            latency_ms=latency_ms,
            cost_microusd=cost_microusd,
            completed_at=self._clock(),
        )
        completed = invocation.model_copy(
            update={"status": ComponentInvocationStatus.FAILED, "result": result}
        )
        return self._repository.complete_invocation(
            ComponentInvocationRecord.model_validate(completed.model_dump(mode="python"))
        )

    def replay(
        self,
        invocation_id: UUID,
        *,
        configuration_json: str | None,
        trace_id: UUID,
    ) -> ComponentInvocationRecord:
        original = self.get_invocation(invocation_id)
        if original.status is ComponentInvocationStatus.PENDING:
            raise InvalidEvaluationState("a pending component invocation cannot be replayed")
        replay = ComponentInvocationRecord(
            invocation_id=uuid4(),
            run_id=original.run_id,
            component=original.component,
            status=ComponentInvocationStatus.PENDING,
            input_artifacts=original.input_artifacts,
            parent_invocation_ids=original.parent_invocation_ids,
            replay_of_invocation_id=original.invocation_id,
            configuration_json=configuration_json or original.configuration_json,
            trace_id=trace_id,
            created_at=self._clock(),
        )
        return self._repository.create_replay(replay)

    def publish(
        self,
        run_id: UUID,
        *,
        output_schema: OutputSchemaIdentifier,
        normalized_output_json: str,
        values: tuple[PublishedPipelineValue, ...],
        output_artifact_ids: tuple[UUID, ...],
        trace_id: UUID,
    ) -> PipelinePublicationRecord:
        detail = self.get_run_detail(run_id)
        self._schemas.validate_json(output_schema, normalized_output_json)
        invocations = {item.invocation_id: item for item in detail.invocations}
        published_document = json.loads(normalized_output_json)
        for value in values:
            invocation = invocations.get(value.producing_invocation_id)
            if invocation is None:
                raise InvalidEvaluationState(
                    "published values must reference invocations from the pipeline run"
                )
            if invocation.status is not ComponentInvocationStatus.SUCCEEDED or not isinstance(
                invocation.result, SucceededComponentResult
            ):
                raise InvalidEvaluationState(
                    "published values must reference successful component invocations"
                )
            source_document = json.loads(invocation.result.normalized_output_json)
            if _resolve_json_pointer(source_document, value.result_path) != json.loads(
                value.value_json
            ):
                raise InvalidEvaluationState(
                    "published value does not match its producing invocation result"
                )
            if _resolve_json_pointer(published_document, value.field_path) != json.loads(
                value.value_json
            ):
                raise InvalidEvaluationState(
                    "published value does not match the final normalized output"
                )
            if not set(value.evidence).issubset(set(invocation.result.evidence)):
                raise InvalidEvaluationState(
                    "published evidence must come from the producing invocation"
                )
        output_artifacts = tuple(
            self._artifact_reference(artifact_id) for artifact_id in output_artifact_ids
        )
        available_outputs = {
            artifact
            for invocation in detail.invocations
            if invocation.result is not None
            for artifact in invocation.result.output_artifacts
        }
        if not set(output_artifacts).issubset(available_outputs):
            raise InvalidEvaluationState(
                "published output artifacts must come from component invocations in the run"
            )
        now = self._clock()
        publication = PipelinePublicationRecord(
            publication_id=uuid4(),
            run_id=detail.run.run_id,
            case_id=detail.run.case_id,
            pipeline=detail.run.pipeline,
            output_schema=output_schema,
            normalized_output_json=normalized_output_json,
            values=values,
            output_artifacts=output_artifacts,
            trace_id=trace_id,
            created_at=now,
        )
        updated_run = detail.run.model_copy(
            update={"status": PipelineRunStatus.PUBLISHED, "updated_at": now}
        )
        return self._repository.publish(
            publication,
            PipelineRunRecord.model_validate(updated_run.model_dump(mode="python")),
        )

    def _pending_invocation(self, invocation_id: UUID) -> ComponentInvocationRecord:
        invocation = self.get_invocation(invocation_id)
        if invocation.status is not ComponentInvocationStatus.PENDING:
            raise ConcurrencyConflict("component invocation already has a terminal result")
        return invocation

    def _ordered_invocations(
        self,
        run: PipelineRunRecord,
    ) -> tuple[ComponentInvocationRecord, ...]:
        definition = self.get_definition(run.definition_id)
        component_order = {
            component.component_key: position
            for position, component in enumerate(definition.components)
        }
        return tuple(
            sorted(
                self._repository.list_invocations(run.run_id),
                key=lambda item: (
                    component_order.get(item.component.component_key, len(component_order)),
                    item.created_at,
                    str(item.invocation_id),
                ),
            )
        )

    def _artifact_reference(self, artifact_id: UUID) -> ArtifactReference:
        artifact = self._artifacts.get_artifact(artifact_id)
        return ArtifactReference(
            artifact_id=artifact.artifact_id,
            sha256=artifact.sha256,
            media_type=artifact.media_type,
            byte_size=artifact.byte_size,
        )

    def _validate_parent_results(self, invocation: ComponentInvocationRecord) -> None:
        for parent_id in invocation.parent_invocation_ids:
            parent = self.get_invocation(parent_id)
            if parent.run_id != invocation.run_id:
                raise InvalidEvaluationState(
                    "component parent must belong to the same pipeline run"
                )
            if parent.status is not ComponentInvocationStatus.SUCCEEDED:
                raise InvalidEvaluationState(
                    "component invocation cannot complete before all parents succeed"
                )

    def _validate_result_references(
        self,
        invocation: ComponentInvocationRecord,
        outputs: tuple[ArtifactReference, ...],
        evidence: tuple[ComponentEvidenceReference, ...],
        issues: tuple[ValidationIssue, ...],
    ) -> None:
        if len(outputs) != len({item.artifact_id for item in outputs}):
            raise InvalidEvaluationState("component output artifact IDs must be unique")
        run = self.get_run(invocation.run_id)
        allowed_ids = {
            *(artifact.artifact_id for artifact in run.input_artifacts),
            *(artifact.artifact_id for artifact in outputs),
        }
        for reference in evidence:
            canonical = self._artifact_reference(reference.artifact.artifact_id)
            if reference.artifact != canonical:
                raise InvalidEvaluationState(
                    "component evidence artifact metadata is not canonical"
                )
            if reference.artifact.artifact_id not in allowed_ids:
                raise InvalidEvaluationState(
                    "component evidence must reference a run input or component output"
                )
        issue_artifact_ids = {
            artifact_id for issue in issues for artifact_id in issue.evidence_artifact_ids
        }
        if not issue_artifact_ids.issubset(allowed_ids):
            raise InvalidEvaluationState(
                "validation issues must reference run inputs or component outputs"
            )


def _resolve_json_pointer(document: object, pointer: str) -> object:
    current = document
    if pointer == "":
        return current
    for raw_token in pointer.removeprefix("/").split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, list):
                current = current[int(token)]
            elif isinstance(current, dict):
                current = current[token]
            else:
                raise KeyError(token)
        except (KeyError, IndexError, ValueError) as exc:
            raise InvalidEvaluationState(f"JSON pointer {pointer!r} does not exist") from exc
    return current
