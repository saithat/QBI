from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceArtifact,
    ComponentInvocationStatus,
    InvocationFailureKind,
    PipelineIdentifier,
    PublishedPipelineValue,
    SpatialAnnotationSet,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    InvalidEvaluationState,
    PipelineRegistryService,
)

from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.pipeline import pipeline_definition
from tests.fakes.pipeline_registry import InMemoryPipelineRunRepository
from tests.fakes.structured import structured_annotation
from tests.fakes.workbench import InMemoryArtifactLookup

NOW = datetime(2026, 8, 2, 21, tzinfo=UTC)


def make_service():
    input_artifact = _artifact("a", "source.png")
    output_artifact = _artifact("b", "normalized.json", media_type="application/json")
    evaluation = EvaluationService(InMemoryEvaluationRepository(), clock=lambda: NOW)
    case = evaluation.create_case(
        case_key=f"pipeline:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=input_artifact.artifact_id,
                role=CaseArtifactRole.FIGURE,
            ),
        ),
    )
    repository = InMemoryPipelineRunRepository()
    service = PipelineRegistryService(
        repository,
        evaluation,
        InMemoryArtifactLookup((input_artifact, output_artifact)),
        clock=lambda: NOW,
    )
    fixture = pipeline_definition()
    definition = service.register_definition(
        pipeline=fixture.pipeline,
        description=fixture.description,
        components=fixture.components,
        configuration_json=fixture.configuration_json,
    )
    return service, repository, case, input_artifact, output_artifact, definition


def create_run(service, case, input_artifact, definition):
    return service.create_run(
        case.case_id,
        definition_id=definition.definition_id,
        input_artifact_ids=(input_artifact.artifact_id,),
        configuration_json='{"mode":"test"}',
        trace_id=uuid4(),
    )


def test_registry_creates_versioned_runs_and_parent_invocations() -> None:
    service, _, case, input_artifact, _, definition = make_service()
    first = create_run(service, case, input_artifact, definition)
    fixture = pipeline_definition()
    second_definition = service.register_definition(
        pipeline=PipelineIdentifier(name=fixture.pipeline.name, version="2.0"),
        description="Candidate graph",
        components=fixture.components,
        configuration_json="{}",
    )
    second = create_run(service, case, input_artifact, second_definition)

    assert first.run.run_id != second.run.run_id
    assert first.run.pipeline.version == "1.0"
    assert second.run.pipeline.version == "2.0"
    assert first.invocations[1].parent_invocation_ids == (first.invocations[0].invocation_id,)
    assert len(service.list_runs(case.case_id)) == 2


def test_successful_result_is_immutable_and_replay_gets_a_new_identity() -> None:
    service, _, case, input_artifact, _, definition = make_service()
    detail = create_run(service, case, input_artifact, definition)
    invocation = detail.invocations[0]
    output = SpatialAnnotationSet().model_dump_json()
    completed = service.complete_success(
        invocation.invocation_id,
        output_schema=invocation.component.output_schema,
        raw_output_json="raw detector output",
        normalized_output_json=output,
        output_artifact_ids=(),
        evidence=(),
        validation_issues=(),
        latency_ms=12,
        cost_microusd=3,
    )
    with pytest.raises(ConcurrencyConflict):
        service.complete_success(
            invocation.invocation_id,
            output_schema=invocation.component.output_schema,
            raw_output_json="replacement",
            normalized_output_json=output,
            output_artifact_ids=(),
            evidence=(),
            validation_issues=(),
            latency_ms=1,
            cost_microusd=0,
        )
    replay = service.replay(
        invocation.invocation_id,
        configuration_json='{"threshold":0.7}',
        trace_id=uuid4(),
    )

    assert completed.status is ComponentInvocationStatus.SUCCEEDED
    assert service.get_invocation(invocation.invocation_id) == completed
    assert replay.invocation_id != invocation.invocation_id
    assert replay.replay_of_invocation_id == invocation.invocation_id
    assert replay.status is ComponentInvocationStatus.PENDING
    assert len(service.get_run_detail(detail.run.run_id).invocations) == 3


def test_invalid_normalized_output_is_recorded_as_a_failed_result() -> None:
    service, _, case, input_artifact, _, definition = make_service()
    detail = create_run(service, case, input_artifact, definition)
    first, second = detail.invocations
    service.complete_success(
        first.invocation_id,
        output_schema=first.component.output_schema,
        raw_output_json="raw regions",
        normalized_output_json=SpatialAnnotationSet().model_dump_json(),
        output_artifact_ids=(),
        evidence=(),
        validation_issues=(),
        latency_ms=2,
        cost_microusd=0,
    )
    failed = service.complete_success(
        second.invocation_id,
        output_schema=second.component.output_schema,
        raw_output_json="raw malformed model output",
        normalized_output_json='{"schema_version":"1.0","unknown":true}',
        output_artifact_ids=(),
        evidence=(),
        validation_issues=(),
        latency_ms=5,
        cost_microusd=9,
    )

    assert failed.status is ComponentInvocationStatus.FAILED
    assert failed.result is not None
    assert failed.result.failure_kind is InvocationFailureKind.OUTPUT_VALIDATION
    assert failed.result.raw_output_json == "raw malformed model output"
    assert failed.result.validation_issues[-1].code == "output_schema_validation_failed"


def test_publication_links_final_values_to_their_producing_invocation() -> None:
    service, _, case, input_artifact, output_artifact, definition = make_service()
    detail = create_run(service, case, input_artifact, definition)
    first, second = detail.invocations
    service.complete_success(
        first.invocation_id,
        output_schema=first.component.output_schema,
        raw_output_json="raw regions",
        normalized_output_json=SpatialAnnotationSet().model_dump_json(),
        output_artifact_ids=(),
        evidence=(),
        validation_issues=(),
        latency_ms=2,
        cost_microusd=0,
    )
    normalized = structured_annotation("TP53").model_dump_json()
    completed = service.complete_success(
        second.invocation_id,
        output_schema=second.component.output_schema,
        raw_output_json="raw metadata",
        normalized_output_json=normalized,
        output_artifact_ids=(output_artifact.artifact_id,),
        evidence=(),
        validation_issues=(),
        latency_ms=8,
        cost_microusd=4,
    )
    value = PublishedPipelineValue(
        field_path="",
        producing_invocation_id=completed.invocation_id,
        result_path="",
        value_json=normalized,
    )
    publication = service.publish(
        detail.run.run_id,
        output_schema=completed.component.output_schema,
        normalized_output_json=normalized,
        values=(value,),
        output_artifact_ids=(output_artifact.artifact_id,),
        trace_id=uuid4(),
    )

    assert publication.values[0].producing_invocation_id == completed.invocation_id
    assert service.get_run(detail.run.run_id).status.value == "published"
    assert service.get_invocation(first.invocation_id).status.value == "succeeded"

    mismatched = value.model_copy(update={"value_json": '"invented"'})
    with pytest.raises(InvalidEvaluationState, match="does not match"):
        service.publish(
            detail.run.run_id,
            output_schema=completed.component.output_schema,
            normalized_output_json=normalized,
            values=(mismatched,),
            output_artifact_ids=(),
            trace_id=uuid4(),
        )


def test_downstream_component_cannot_complete_before_its_parent() -> None:
    service, _, case, input_artifact, _, definition = make_service()
    detail = create_run(service, case, input_artifact, definition)
    downstream = detail.invocations[1]

    with pytest.raises(InvalidEvaluationState, match="before all parents succeed"):
        service.complete_failure(
            downstream.invocation_id,
            failure_kind=InvocationFailureKind.EXECUTION,
            error_code="not_ready",
            error_message="parent has not completed",
            raw_output_json=None,
            normalized_output_json=None,
            output_artifact_ids=(),
            evidence=(),
            validation_issues=(),
            latency_ms=0,
            cost_microusd=0,
        )


def _artifact(seed: str, filename: str, *, media_type: str = "image/png") -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=uuid4(),
        sha256=seed * 64,
        media_type=media_type,
        byte_size=128,
        original_filename=filename,
        source_uri=f"urn:hiveblot:test:{filename}",
        acquisition_method=ArtifactAcquisitionMethod.SOURCE_ADAPTER,
        visibility=ArtifactVisibility.PUBLIC,
        created_at=NOW,
    )
