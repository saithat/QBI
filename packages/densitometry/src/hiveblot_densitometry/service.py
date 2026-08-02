"""Versioned deterministic densitometry orchestration and replay."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactRecord,
    ArtifactReference,
    ArtifactRelationship,
    ArtifactRelationshipKind,
    ComponentEvidenceReference,
    ComponentInvocationRecord,
    ComponentInvocationStatus,
    DensitometryConfiguration,
    DensitometryGeometryReference,
    DensitometryInput,
    DensitometryQcSeverity,
    DensitometryReplayRecord,
    DensitometryResult,
    DensitometryRunRecord,
    InvocationFailureKind,
    OutputSchemaIdentifier,
    PipelineIdentifier,
    PublishedPipelineValue,
    SucceededComponentResult,
    ToolPipelineComponent,
    ValidationIssue,
    ValidationSeverity,
)
from hiveblot_evaluation import (
    DuplicateEvaluationRecord,
    EvaluationNotFound,
    PipelineRegistryService,
)
from hiveblot_storage import ArtifactReader, ArtifactService
from pydantic import BaseModel

from .errors import DensitometryRunNotFound, InvalidDensitometryInput
from .geometry import DensitometryGeometryOption, DensitometryGeometryResolver
from .tool import DeterministicDensitometryTool

DENSITOMETRY_COMPONENT = "densitometry_measurement"
DENSITOMETRY_PIPELINE = PipelineIdentifier(
    name="western-blot-densitometry",
    version="1.0.0",
)
DENSITOMETRY_RESULT_SCHEMA = OutputSchemaIdentifier(
    name="densitometry-result",
    version="1.0",
)


@dataclass(frozen=True, slots=True)
class StoredDensitometryAttempt:
    run_id: UUID
    definition_id: UUID
    case_id: UUID
    invocation_id: UUID
    replay_of_invocation_id: UUID | None
    publication_id: UUID
    trace_id: UUID
    created_at: datetime
    result: DensitometryResult


class DensitometryService:
    def __init__(
        self,
        artifacts: ArtifactReader,
        artifact_service: ArtifactService,
        pipelines: PipelineRegistryService,
        geometry: DensitometryGeometryResolver,
        tool: DeterministicDensitometryTool | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._artifact_service = artifact_service
        self._pipelines = pipelines
        self._geometry = geometry
        self._tool = tool or DeterministicDensitometryTool()

    def geometry_options(self, case_id: UUID) -> tuple[DensitometryGeometryOption, ...]:
        return self._geometry.list_options(case_id)

    def run(
        self,
        case_id: UUID,
        *,
        image_artifact_id: UUID,
        geometry: DensitometryGeometryReference,
        loading_control_target_id: UUID | None,
        configuration: DensitometryConfiguration,
        trace_id: UUID | None = None,
    ) -> DensitometryRunRecord:
        trace_id = trace_id or uuid4()
        densitometry_input = self._geometry.build_input(
            case_id,
            image_artifact_id=image_artifact_id,
            geometry=geometry,
            loading_control_target_id=loading_control_target_id,
            configuration=configuration,
        )
        definition = self._definition()
        detail = self._pipelines.create_run(
            case_id,
            definition_id=definition.definition_id,
            input_artifact_ids=(image_artifact_id,),
            configuration_json=_json(densitometry_input),
            trace_id=trace_id,
        )
        invocation = detail.invocations[0]
        result, publication_id = self._execute_and_publish(
            invocation,
            densitometry_input,
            trace_id=trace_id,
        )
        return DensitometryRunRecord(
            run_id=detail.run.run_id,
            definition_id=definition.definition_id,
            case_id=case_id,
            invocation_id=invocation.invocation_id,
            publication_id=publication_id,
            trace_id=trace_id,
            pipeline=DENSITOMETRY_PIPELINE,
            result=result,
        )

    def replay(
        self,
        invocation_id: UUID,
        *,
        trace_id: UUID | None = None,
    ) -> DensitometryReplayRecord:
        trace_id = trace_id or uuid4()
        original = self._pipelines.get_invocation(invocation_id)
        run = self._pipelines.get_run(original.run_id)
        if run.pipeline != DENSITOMETRY_PIPELINE:
            raise DensitometryRunNotFound("invocation is not a densitometry pipeline attempt")
        if original.component.component_key != DENSITOMETRY_COMPONENT:
            raise DensitometryRunNotFound("invocation is not a densitometry component")
        if len(run.input_artifacts) != 1:
            raise InvalidDensitometryInput("densitometry runs require one source image")
        densitometry_input = DensitometryInput.model_validate_json(run.configuration_json)
        if run.input_artifacts[0] != densitometry_input.image_artifact:
            raise InvalidDensitometryInput("stored run input artifact does not match its contract")
        replay = self._pipelines.replay(
            invocation_id,
            configuration_json=None,
            trace_id=trace_id,
        )
        result, publication_id = self._execute_and_publish(
            replay,
            densitometry_input,
            trace_id=trace_id,
        )
        return DensitometryReplayRecord(
            run_id=run.run_id,
            case_id=run.case_id,
            invocation_id=replay.invocation_id,
            replay_of_invocation_id=invocation_id,
            publication_id=publication_id,
            trace_id=trace_id,
            result=result,
        )

    def list_attempts(self, case_id: UUID) -> tuple[StoredDensitometryAttempt, ...]:
        attempts: list[StoredDensitometryAttempt] = []
        for detail in self._pipelines.list_runs(case_id):
            if detail.run.pipeline != DENSITOMETRY_PIPELINE:
                continue
            publications_by_invocation = {
                value.producing_invocation_id: publication
                for publication in detail.publications
                for value in publication.values
                if value.field_path == ""
            }
            for invocation in detail.invocations:
                if not isinstance(invocation.result, SucceededComponentResult):
                    continue
                publication = publications_by_invocation.get(invocation.invocation_id)
                if publication is None:
                    continue
                result = DensitometryResult.model_validate_json(
                    invocation.result.normalized_output_json
                )
                attempts.append(
                    StoredDensitometryAttempt(
                        run_id=detail.run.run_id,
                        definition_id=detail.run.definition_id,
                        case_id=detail.run.case_id,
                        invocation_id=invocation.invocation_id,
                        replay_of_invocation_id=invocation.replay_of_invocation_id,
                        publication_id=publication.publication_id,
                        trace_id=invocation.trace_id,
                        created_at=invocation.result.completed_at,
                        result=result,
                    )
                )
        return tuple(sorted(attempts, key=lambda item: item.created_at, reverse=True))

    def get_attempt(self, invocation_id: UUID) -> StoredDensitometryAttempt:
        invocation = self._pipelines.get_invocation(invocation_id)
        run = self._pipelines.get_run(invocation.run_id)
        if run.pipeline != DENSITOMETRY_PIPELINE:
            raise DensitometryRunNotFound("invocation is not a densitometry pipeline attempt")
        for item in self.list_attempts(run.case_id):
            if item.invocation_id == invocation_id:
                return item
        raise DensitometryRunNotFound(f"densitometry attempt {invocation_id} is not published")

    def _execute_and_publish(
        self,
        invocation: ComponentInvocationRecord,
        densitometry_input: DensitometryInput,
        *,
        trace_id: UUID,
    ) -> tuple[DensitometryResult, UUID]:
        started = time.perf_counter()
        raw_output_json: str | None = None
        try:
            source = self._artifacts.get_artifact(densitometry_input.image_artifact.artifact_id)
            image_bytes = self._artifacts.read_bytes(source.artifact_id)
            computation = self._tool.analyze(densitometry_input, image_bytes)
            raw_output_json = computation.raw_output_json
            published = self._artifact_service.publish_tool_output(
                original_filename=f"densitometry-overlay-{computation.input_sha256[:16]}.png",
                declared_media_type="image/png",
                content=computation.overlay_png,
                source_uri=(
                    f"urn:hiveblot:densitometry:{self._tool.identity.version}:"
                    f"{computation.input_sha256}"
                ),
                visibility=source.visibility,
                organization_id=source.organization_id,
                relationships=(
                    ArtifactRelationship(
                        related_artifact_id=source.artifact_id,
                        kind=ArtifactRelationshipKind.DERIVED_FROM,
                    ),
                ),
                actor_id=None,
            )
            result = computation.result(_artifact_reference(published.artifact))
            evidence = self._evidence(densitometry_input)
            latency_ms = _elapsed_ms(started)
            completed = self._pipelines.complete_success(
                invocation.invocation_id,
                output_schema=DENSITOMETRY_RESULT_SCHEMA,
                raw_output_json=raw_output_json,
                normalized_output_json=_json(result),
                output_artifact_ids=(published.artifact.artifact_id,),
                evidence=evidence,
                validation_issues=_validation_issues(result),
                latency_ms=latency_ms,
                cost_microusd=0,
            )
            if completed.status is not ComponentInvocationStatus.SUCCEEDED:
                raise InvalidDensitometryInput(
                    "densitometry result failed strict pipeline output validation"
                )
            publication = self._pipelines.publish(
                invocation.run_id,
                output_schema=DENSITOMETRY_RESULT_SCHEMA,
                normalized_output_json=_json(result),
                values=(
                    PublishedPipelineValue(
                        field_path="",
                        producing_invocation_id=invocation.invocation_id,
                        result_path="",
                        value_json=_json(result),
                        evidence=evidence,
                    ),
                ),
                output_artifact_ids=(published.artifact.artifact_id,),
                trace_id=trace_id,
            )
            return result, publication.publication_id
        except Exception as exc:
            current = self._pipelines.get_invocation(invocation.invocation_id)
            if current.status is ComponentInvocationStatus.PENDING:
                self._pipelines.complete_failure(
                    invocation.invocation_id,
                    failure_kind=InvocationFailureKind.EXECUTION,
                    error_code="densitometry_execution_failed",
                    error_message=(str(exc) or exc.__class__.__name__)[:8000],
                    raw_output_json=raw_output_json,
                    normalized_output_json=None,
                    output_artifact_ids=(),
                    evidence=self._evidence(densitometry_input),
                    validation_issues=(),
                    latency_ms=_elapsed_ms(started),
                    cost_microusd=0,
                )
            raise

    def _definition(self):
        try:
            return self._pipelines.get_definition_by_pipeline(DENSITOMETRY_PIPELINE)
        except EvaluationNotFound:
            pass
        component = ToolPipelineComponent(
            component_key=DENSITOMETRY_COMPONENT,
            output_schema=DENSITOMETRY_RESULT_SCHEMA,
            configuration_json=_json(self._tool.identity),
            tool=self._tool.identity,
        )
        try:
            return self._pipelines.register_definition(
                pipeline=DENSITOMETRY_PIPELINE,
                description=(
                    "Deterministic integrated-darkness measurement with explicit background, "
                    "normalization, quality control, and immutable overlay publication."
                ),
                components=(component,),
                configuration_json=_json(self._tool.identity),
            )
        except DuplicateEvaluationRecord:
            return self._pipelines.get_definition_by_pipeline(DENSITOMETRY_PIPELINE)

    def _evidence(
        self,
        densitometry_input: DensitometryInput,
    ) -> tuple[ComponentEvidenceReference, ...]:
        return tuple(
            self._pipelines.evidence_reference(
                artifact_id=densitometry_input.image_artifact.artifact_id,
                field_path=f"/input/bands/{index}",
                region=band.region,
                description="densitometry band measurement region",
            )
            for index, band in enumerate(densitometry_input.bands)
        )


def _validation_issues(result: DensitometryResult) -> tuple[ValidationIssue, ...]:
    flags = (
        *result.global_qc_flags,
        *(flag for measurement in result.measurements for flag in measurement.qc_flags),
    )
    return tuple(
        ValidationIssue(
            issue_id=flag.flag_id,
            severity=(
                ValidationSeverity.ERROR
                if flag.severity is DensitometryQcSeverity.ERROR
                else ValidationSeverity.WARNING
            ),
            code=f"densitometry_{flag.code.value}",
            message=flag.message,
            evidence_artifact_ids=(result.input.image_artifact.artifact_id,),
        )
        for flag in flags
    )


def _artifact_reference(value: ArtifactRecord) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=value.artifact_id,
        sha256=value.sha256,
        media_type=value.media_type,
        byte_size=value.byte_size,
    )


def _json(value: BaseModel) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
