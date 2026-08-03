"""Synchronous PRD-011 orchestration over immutable pipeline/evaluation services."""

from __future__ import annotations

import json
import time
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactRecord,
    ArtifactReference,
    CaseArtifactRole,
    CaseSourceArtifact,
    ComponentEvidenceReference,
    ComponentInvocationRecord,
    ComponentInvocationStatus,
    InvocationFailureKind,
    ModelPipelineComponent,
    OutputSchemaIdentifier,
    PredictionEvidence,
    PublishedPipelineValue,
    SucceededComponentResult,
    ToolPipelineComponent,
    ValidationIssue,
    ValidationSeverity,
    WesternBlotCandidatePredictionSet,
    WesternBlotComponentReplayRecord,
    WesternBlotExtractionConfiguration,
    WesternBlotExtractionInput,
    WesternBlotExtractionResult,
    WesternBlotExtractionRunRecord,
    WesternBlotFigureCandidateSet,
    WesternBlotSourceKind,
)
from hiveblot_evaluation import (
    DuplicateEvaluationRecord,
    EvaluationNotFound,
    EvaluationService,
    InvalidEvaluationState,
    PipelineRegistryService,
)

from .artifacts import ExtractionArtifactReader
from .assembly import assemble_extraction_result
from .errors import ExtractionOutputInvalid, UnsupportedExtractionSource
from .implementations import (
    DetectionExecution,
    ExtractionImplementationRegistry,
    ModelExecution,
    WesternBlotExtractionImplementationAdapter,
)
from .normalization import normalize_candidate_predictions

DETECT_COMPONENT = "figure_detection"
MODEL_COMPONENT = "western_blot_vision"
ASSEMBLE_COMPONENT = "case_assembly"
FIGURE_CANDIDATE_SCHEMA = OutputSchemaIdentifier(
    name="western-blot-figure-candidate-set",
    version="1.0",
)
CANDIDATE_PREDICTION_SCHEMA = OutputSchemaIdentifier(
    name="western-blot-candidate-prediction-set",
    version="1.0",
)
EXTRACTION_RESULT_SCHEMA = OutputSchemaIdentifier(
    name="western-blot-extraction-result",
    version="1.0",
)


class WesternBlotExtractionService:
    def __init__(
        self,
        artifacts: ExtractionArtifactReader,
        evaluation: EvaluationService,
        pipelines: PipelineRegistryService,
        implementations: ExtractionImplementationRegistry,
    ) -> None:
        self._artifacts = artifacts
        self._evaluation = evaluation
        self._pipelines = pipelines
        self._implementations = implementations

    def list_implementations(self):
        return self._implementations.list()

    def run(
        self,
        source_artifact_id: UUID,
        *,
        configuration: WesternBlotExtractionConfiguration,
        trace_id: UUID | None = None,
    ) -> WesternBlotExtractionRunRecord:
        trace_id = trace_id or uuid4()
        artifact = self._artifacts.get_artifact(source_artifact_id)
        source_kind = _source_kind(artifact)
        content = self._artifacts.read_bytes(source_artifact_id)
        implementation = self._implementations.get(configuration.implementation_name)
        extraction_input = WesternBlotExtractionInput(
            source_artifact=_reference(artifact),
            source_kind=source_kind,
            configuration=configuration,
            trace_id=trace_id,
        )
        case = self._case_for_artifact(artifact, source_kind)
        definition = self._definition(implementation)
        detail = self._pipelines.create_run(
            case.case_id,
            definition_id=definition.definition_id,
            input_artifact_ids=(artifact.artifact_id,),
            configuration_json=_json(configuration),
            trace_id=trace_id,
            visibility=artifact.visibility,
            organization_id=artifact.organization_id,
        )
        invocations = {item.component.component_key: item for item in detail.invocations}
        detector_invocation = invocations[DETECT_COMPONENT]
        model_invocation = invocations[MODEL_COMPONENT]
        assembler_invocation = invocations[ASSEMBLE_COMPONENT]

        detection = self._execute_detection(
            detector_invocation,
            implementation,
            extraction_input,
            content,
        )
        model_execution, predictions, raw_model_output = self._execute_model(
            model_invocation,
            implementation,
            extraction_input,
            content,
            detection.result,
        )
        result, assembly_latency = self._execute_assembly(
            assembler_invocation,
            case_id=case.case_id,
            implementation=implementation,
            predictions=predictions,
        )
        prediction_id, publication_id = self._publish_result(
            result,
            assembler_invocation_id=assembler_invocation.invocation_id,
            run_id=detail.run.run_id,
            raw_model_output=raw_model_output,
            configuration=configuration,
            trace_id=trace_id,
            latency_ms=detection.latency_ms + model_execution.latency_ms + assembly_latency,
            cost_microusd=model_execution.cost_microusd,
        )
        return WesternBlotExtractionRunRecord(
            run_id=detail.run.run_id,
            definition_id=definition.definition_id,
            case_id=case.case_id,
            detector_invocation_id=detector_invocation.invocation_id,
            model_invocation_id=model_invocation.invocation_id,
            assembler_invocation_id=assembler_invocation.invocation_id,
            prediction_id=prediction_id,
            publication_id=publication_id,
            trace_id=trace_id,
            pipeline=implementation.identity.pipeline,
            result=result,
        )

    def replay_component(
        self,
        invocation_id: UUID,
        *,
        trace_id: UUID | None = None,
    ) -> WesternBlotComponentReplayRecord:
        trace_id = trace_id or uuid4()
        original = self._pipelines.get_invocation(invocation_id)
        run = self._pipelines.get_run(original.run_id)
        configuration = WesternBlotExtractionConfiguration.model_validate_json(
            run.configuration_json
        )
        implementation = self._implementations.get(configuration.implementation_name)
        if run.pipeline != implementation.identity.pipeline:
            raise InvalidEvaluationState(
                "the exact extraction implementation version for this run is not configured"
            )
        if len(run.input_artifacts) != 1:
            raise InvalidEvaluationState("western-blot extraction runs require one source artifact")
        if original.component.component_key not in {
            DETECT_COMPONENT,
            MODEL_COMPONENT,
            ASSEMBLE_COMPONENT,
        }:
            raise InvalidEvaluationState("invocation is not a western-blot extraction component")
        artifact_id = run.input_artifacts[0].artifact_id
        artifact = self._artifacts.get_artifact(artifact_id)
        extraction_input = WesternBlotExtractionInput(
            source_artifact=_reference(artifact),
            source_kind=_source_kind(artifact),
            configuration=configuration,
            trace_id=trace_id,
        )
        content = self._artifacts.read_bytes(artifact_id)
        replay = self._pipelines.replay(invocation_id, configuration_json=None, trace_id=trace_id)
        prediction_id: UUID | None = None
        publication_id: UUID | None = None

        if replay.component.component_key == DETECT_COMPONENT:
            self._execute_detection(replay, implementation, extraction_input, content)
        elif replay.component.component_key == MODEL_COMPONENT:
            candidates = self._parent_result(replay, WesternBlotFigureCandidateSet)
            self._execute_model(
                replay,
                implementation,
                extraction_input,
                content,
                candidates,
            )
        else:
            predictions = self._parent_result(replay, WesternBlotCandidatePredictionSet)
            result, latency_ms = self._execute_assembly(
                replay,
                case_id=run.case_id,
                implementation=implementation,
                predictions=predictions,
            )
            parent = self._pipelines.get_invocation(replay.parent_invocation_ids[0])
            if not isinstance(parent.result, SucceededComponentResult):
                raise InvalidEvaluationState("assembly replay requires a successful model parent")
            prediction_id, publication_id = self._publish_result(
                result,
                assembler_invocation_id=replay.invocation_id,
                run_id=run.run_id,
                raw_model_output=parent.result.raw_output_json,
                configuration=configuration,
                trace_id=trace_id,
                latency_ms=latency_ms,
                cost_microusd=parent.result.cost_microusd,
            )
        return WesternBlotComponentReplayRecord(
            run_id=run.run_id,
            case_id=run.case_id,
            invocation_id=replay.invocation_id,
            replay_of_invocation_id=invocation_id,
            component_key=replay.component.component_key,
            trace_id=trace_id,
            prediction_id=prediction_id,
            publication_id=publication_id,
        )

    def _execute_detection(
        self,
        invocation: ComponentInvocationRecord,
        implementation: WesternBlotExtractionImplementationAdapter,
        extraction_input: WesternBlotExtractionInput,
        content: bytes,
    ) -> DetectionExecution:
        try:
            execution = implementation.detect(extraction_input, content)
        except Exception as exc:
            self._record_execution_failure(invocation, "figure_detection_failed", exc)
            raise
        issues = (
            (
                ValidationIssue(
                    issue_id=uuid4(),
                    severity=ValidationSeverity.WARNING,
                    code="no_figure_candidates",
                    message="The detector produced no candidates above the configured threshold.",
                    evidence_artifact_ids=(extraction_input.source_artifact.artifact_id,),
                ),
            )
            if not execution.result.candidates
            else ()
        )
        completed = self._pipelines.complete_success(
            invocation.invocation_id,
            output_schema=FIGURE_CANDIDATE_SCHEMA,
            raw_output_json=execution.raw_output_json,
            normalized_output_json=_json(execution.result),
            output_artifact_ids=(),
            evidence=self._candidate_evidence(execution.result),
            validation_issues=issues,
            latency_ms=execution.latency_ms,
            cost_microusd=0,
        )
        _require_succeeded(completed)
        return execution

    def _execute_model(
        self,
        invocation: ComponentInvocationRecord,
        implementation: WesternBlotExtractionImplementationAdapter,
        extraction_input: WesternBlotExtractionInput,
        content: bytes,
        candidates: WesternBlotFigureCandidateSet,
    ) -> tuple[ModelExecution, WesternBlotCandidatePredictionSet, str]:
        try:
            execution = implementation.infer(extraction_input, content, candidates)
        except Exception as exc:
            self._record_execution_failure(invocation, "vision_extraction_failed", exc)
            raise
        raw_model_output = _raw_model_envelope(execution)
        raw_responses = {item.candidate_id: item.raw_output for item in execution.responses}
        cost_issues = (
            ()
            if execution.cost_is_complete
            else (
                ValidationIssue(
                    issue_id=uuid4(),
                    severity=ValidationSeverity.WARNING,
                    code="model_cost_unavailable",
                    message=(
                        "The model provider did not report a monetary cost for this invocation."
                    ),
                    evidence_artifact_ids=(extraction_input.source_artifact.artifact_id,),
                ),
            )
        )
        try:
            predictions = normalize_candidate_predictions(
                candidates,
                model=implementation.identity.model,
                prompt_version=implementation.identity.prompt_version,
                raw_responses=raw_responses,
            )
        except ExtractionOutputInvalid as exc:
            failed = self._pipelines.complete_success(
                invocation.invocation_id,
                output_schema=CANDIDATE_PREDICTION_SCHEMA,
                raw_output_json=raw_model_output,
                normalized_output_json=json.dumps(
                    {"schema_version": "1.0", "normalization_error": str(exc)},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                output_artifact_ids=(),
                evidence=self._candidate_evidence(candidates),
                validation_issues=cost_issues,
                latency_ms=execution.latency_ms,
                cost_microusd=execution.cost_microusd,
            )
            if failed.status is not ComponentInvocationStatus.FAILED:
                raise AssertionError(
                    "invalid model output must produce a failed invocation"
                ) from exc
            raise
        completed = self._pipelines.complete_success(
            invocation.invocation_id,
            output_schema=CANDIDATE_PREDICTION_SCHEMA,
            raw_output_json=raw_model_output,
            normalized_output_json=_json(predictions),
            output_artifact_ids=(),
            evidence=self._candidate_evidence(candidates),
            validation_issues=cost_issues,
            latency_ms=execution.latency_ms,
            cost_microusd=execution.cost_microusd,
        )
        _require_succeeded(completed)
        return execution, predictions, raw_model_output

    def _execute_assembly(
        self,
        invocation: ComponentInvocationRecord,
        *,
        case_id: UUID,
        implementation: WesternBlotExtractionImplementationAdapter,
        predictions: WesternBlotCandidatePredictionSet,
    ) -> tuple[WesternBlotExtractionResult, int]:
        started = time.perf_counter()
        try:
            result = assemble_extraction_result(
                case_id=case_id,
                implementation=implementation.identity,
                predictions=predictions,
            )
        except Exception as exc:
            self._record_execution_failure(invocation, "case_assembly_failed", exc)
            raise
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        completed = self._pipelines.complete_success(
            invocation.invocation_id,
            output_schema=EXTRACTION_RESULT_SCHEMA,
            raw_output_json=_json(predictions),
            normalized_output_json=_json(result),
            output_artifact_ids=(),
            evidence=self._result_component_evidence(result),
            validation_issues=result.validation_issues,
            latency_ms=latency_ms,
            cost_microusd=0,
        )
        _require_succeeded(completed)
        return result, latency_ms

    def _publish_result(
        self,
        result: WesternBlotExtractionResult,
        *,
        assembler_invocation_id: UUID,
        run_id: UUID,
        raw_model_output: str,
        configuration: WesternBlotExtractionConfiguration,
        trace_id: UUID,
        latency_ms: int,
        cost_microusd: int,
    ) -> tuple[UUID, UUID]:
        normalized = _json(result)
        prediction_evidence = self._prediction_evidence(result)
        prediction = self._evaluation.add_prediction(
            result.case_id,
            prediction_schema=EXTRACTION_RESULT_SCHEMA.name,
            prediction_schema_version=EXTRACTION_RESULT_SCHEMA.version,
            producer=result.implementation.model,
            pipeline=result.implementation.pipeline,
            raw_output_json=raw_model_output,
            normalized_output_json=normalized,
            configuration_json=_json(configuration),
            evidence=prediction_evidence,
            validation_issues=result.validation_issues,
            confidence=result.confidence,
            trace_id=trace_id,
            latency_ms=latency_ms,
            cost_microusd=cost_microusd,
        )
        component_evidence = self._result_component_evidence(result)
        publication = self._pipelines.publish(
            run_id,
            output_schema=EXTRACTION_RESULT_SCHEMA,
            normalized_output_json=normalized,
            values=(
                PublishedPipelineValue(
                    field_path="",
                    producing_invocation_id=assembler_invocation_id,
                    result_path="",
                    value_json=normalized,
                    evidence=component_evidence,
                ),
            ),
            output_artifact_ids=(),
            trace_id=trace_id,
        )
        return prediction.prediction_id, publication.publication_id

    def _definition(self, implementation: WesternBlotExtractionImplementationAdapter):
        identity = implementation.identity
        try:
            return self._pipelines.get_definition_by_pipeline(identity.pipeline)
        except EvaluationNotFound:
            pass
        components = (
            ToolPipelineComponent(
                component_key=DETECT_COMPONENT,
                output_schema=FIGURE_CANDIDATE_SCHEMA,
                configuration_json=_json(identity.detector),
                tool=identity.detector,
            ),
            ModelPipelineComponent(
                component_key=MODEL_COMPONENT,
                depends_on=(DETECT_COMPONENT,),
                output_schema=CANDIDATE_PREDICTION_SCHEMA,
                configuration_json=json.dumps(
                    {
                        "model": identity.model.model_dump(mode="json"),
                        "prompt_version": identity.prompt_version,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                model=identity.model,
                prompt_version=identity.prompt_version,
            ),
            ToolPipelineComponent(
                component_key=ASSEMBLE_COMPONENT,
                depends_on=(MODEL_COMPONENT,),
                output_schema=EXTRACTION_RESULT_SCHEMA,
                configuration_json=_json(identity.assembler),
                tool=identity.assembler,
            ),
        )
        try:
            return self._pipelines.register_definition(
                pipeline=identity.pipeline,
                description=(
                    "Stored PDF/image western-blot extraction using retained candidate/model "
                    "behavior and deterministic review-case assembly."
                ),
                components=components,
                configuration_json=_json(identity),
            )
        except DuplicateEvaluationRecord:
            return self._pipelines.get_definition_by_pipeline(identity.pipeline)

    def _case_for_artifact(self, artifact: ArtifactRecord, source_kind: WesternBlotSourceKind):
        case_key = f"western-blot:{artifact.artifact_id}"
        existing = self._evaluation.get_case_by_key(case_key)
        if existing is not None:
            if {item.artifact_id for item in existing.source_artifacts} != {artifact.artifact_id}:
                raise InvalidEvaluationState("stable extraction case key references another source")
            if (
                existing.visibility is not artifact.visibility
                or existing.organization_id != artifact.organization_id
            ):
                raise InvalidEvaluationState("extraction case scope does not match its source")
            return existing
        role = (
            CaseArtifactRole.SOURCE_DOCUMENT
            if source_kind is WesternBlotSourceKind.PDF
            else CaseArtifactRole.FIGURE
        )
        try:
            return self._evaluation.create_case(
                case_key=case_key,
                dataset_id=None,
                source_artifacts=(
                    CaseSourceArtifact(
                        artifact_id=artifact.artifact_id,
                        role=role,
                    ),
                ),
                visibility=artifact.visibility,
                organization_id=artifact.organization_id,
            )
        except DuplicateEvaluationRecord:
            concurrent = self._evaluation.get_case_by_key(case_key)
            if concurrent is None:
                raise
            return concurrent

    def _candidate_evidence(
        self,
        candidates: WesternBlotFigureCandidateSet,
    ) -> tuple[ComponentEvidenceReference, ...]:
        return tuple(
            self._pipelines.evidence_reference(
                artifact_id=candidate.source_artifact.artifact_id,
                field_path=f"/candidates/{index}",
                region=candidate.region,
                description="western blot figure candidate",
            )
            for index, candidate in enumerate(candidates.candidates)
        )

    def _result_component_evidence(
        self,
        result: WesternBlotExtractionResult,
    ) -> tuple[ComponentEvidenceReference, ...]:
        return tuple(
            self._pipelines.evidence_reference(
                artifact_id=result.source_artifact.artifact_id,
                field_path=f"/spatial_annotation_set/spatial_annotations/{index}",
                region=item.region,
                description=f"{item.annotation_type.value}: {item.label or 'unlabeled'}",
            )
            for index, item in enumerate(result.spatial_annotation_set.spatial_annotations)
        )

    def _prediction_evidence(
        self,
        result: WesternBlotExtractionResult,
    ) -> tuple[PredictionEvidence, ...]:
        by_id = {
            item.spatial_annotation_id: item
            for item in result.spatial_annotation_set.spatial_annotations
        }
        evidence: list[PredictionEvidence] = []
        for field in result.field_evidence:
            for region_id in field.region_ids:
                annotation = by_id[region_id]
                evidence.append(
                    PredictionEvidence(
                        artifact_id=result.source_artifact.artifact_id,
                        field_path=field.field_path,
                        region_id=region_id,
                        region=annotation.region,
                        description=annotation.label or annotation.annotation_type.value,
                    )
                )
        referenced = {item.region_id for item in evidence}
        for index, annotation in enumerate(result.spatial_annotation_set.spatial_annotations):
            if annotation.spatial_annotation_id in referenced:
                continue
            evidence.append(
                PredictionEvidence(
                    artifact_id=result.source_artifact.artifact_id,
                    field_path=f"/spatial_annotation_set/spatial_annotations/{index}",
                    region_id=annotation.spatial_annotation_id,
                    region=annotation.region,
                    description=(
                        f"{annotation.annotation_type.value}: {annotation.label or 'unlabeled'}"
                    ),
                )
            )
        return tuple(evidence)

    def _parent_result(self, invocation: ComponentInvocationRecord, model):
        if len(invocation.parent_invocation_ids) != 1:
            raise InvalidEvaluationState("extraction components require exactly one parent")
        parent = self._pipelines.get_invocation(invocation.parent_invocation_ids[0])
        if not isinstance(parent.result, SucceededComponentResult):
            raise InvalidEvaluationState("component replay requires a successful parent result")
        return model.model_validate_json(parent.result.normalized_output_json)

    def _record_execution_failure(
        self,
        invocation: ComponentInvocationRecord,
        error_code: str,
        exc: Exception,
    ) -> None:
        self._pipelines.complete_failure(
            invocation.invocation_id,
            failure_kind=InvocationFailureKind.EXECUTION,
            error_code=error_code,
            error_message=(str(exc) or exc.__class__.__name__)[:8000],
            raw_output_json=None,
            normalized_output_json=None,
            output_artifact_ids=(),
            evidence=(),
            validation_issues=(),
            latency_ms=0,
            cost_microusd=0,
        )


def _source_kind(artifact: ArtifactRecord) -> WesternBlotSourceKind:
    if artifact.media_type == "application/pdf":
        return WesternBlotSourceKind.PDF
    if artifact.media_type.startswith("image/"):
        return WesternBlotSourceKind.IMAGE
    raise UnsupportedExtractionSource(
        f"artifact {artifact.artifact_id} has unsupported media type {artifact.media_type}"
    )


def _reference(artifact: ArtifactRecord) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=artifact.artifact_id,
        sha256=artifact.sha256,
        media_type=artifact.media_type,
        byte_size=artifact.byte_size,
    )


def _raw_model_envelope(execution: ModelExecution) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "responses": [
                {
                    "candidate_id": str(item.candidate_id),
                    "raw_output": item.raw_output,
                    "latency_ms": item.latency_ms,
                    "cost_microusd": item.cost_microusd,
                }
                for item in execution.responses
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _json(value) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _require_succeeded(invocation: ComponentInvocationRecord) -> None:
    if invocation.status is not ComponentInvocationStatus.SUCCEEDED:
        raise ExtractionOutputInvalid(
            f"component {invocation.component.component_key} failed strict output validation"
        )
