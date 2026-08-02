import json
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ComponentInvocationStatus,
    InvocationFailureKind,
    ReviewStatus,
    WesternBlotExtractionConfiguration,
    WesternBlotExtractionResult,
)
from hiveblot_evaluation import (
    EvaluationService,
    PipelineRegistryService,
    SpatialAnnotationService,
    StructuredAnnotationService,
)
from hiveblot_extraction import (
    ExtractionImplementationRegistry,
    ExtractionOutputInvalid,
    WesternBlotExtractionService,
)

from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.extraction import (
    FixtureExtractionImplementation,
    InMemoryExtractionArtifactReader,
    extraction_artifact,
)
from tests.fakes.pipeline_registry import InMemoryPipelineRunRepository


def test_extraction_reruns_append_predictions_and_preserve_reviews() -> None:
    service, evaluation, pipelines, implementation, artifact = _service()
    configuration = WesternBlotExtractionConfiguration(
        implementation_name=implementation.identity.implementation_name
    )
    first = service.run(artifact.artifact_id, configuration=configuration)
    document, revision = evaluation.create_annotation(
        first.case_id,
        reviewer_id=uuid4(),
        rationale="Preserved human review.",
        error_codes=(),
        field_annotations=(),
        spatial_annotations=(),
        relationships=(),
        structured_annotation=first.result.structured_annotation,
    )
    current = evaluation.get_case(first.case_id)
    evaluation.update_case_status(
        first.case_id,
        expected_version=current.version,
        review_status=ReviewStatus.REVIEWED,
    )

    second = service.run(artifact.artifact_id, configuration=configuration)

    assert second.case_id == first.case_id
    assert second.run_id != first.run_id
    assert second.prediction_id != first.prediction_id
    assert len(evaluation.list_predictions(first.case_id)) == 2
    assert (
        evaluation.get_annotation(document.annotation_id).head_revision_id == revision.revision_id
    )
    assert len(evaluation.list_revisions(document.annotation_id)) == 1
    assert len(pipelines.list_runs(first.case_id)) == 2
    prediction = evaluation.get_prediction(first.prediction_id)
    assert json.loads(prediction.raw_output_json)["responses"][0]["raw_output"] == (
        implementation.raw_output
    )
    assert (
        WesternBlotExtractionResult.model_validate_json(prediction.normalized_output_json or "")
        == first.result
    )
    assert (
        StructuredAnnotationService(evaluation).prediction_annotation(
            first.case_id, first.prediction_id
        )[1]
        == first.result.structured_annotation
    )
    assert (
        SpatialAnnotationService(evaluation).prediction_set(first.case_id, first.prediction_id)[1]
        == first.result.spatial_annotation_set
    )


def test_assembly_replay_creates_new_prediction_without_replacing_history() -> None:
    service, evaluation, pipelines, implementation, artifact = _service()
    first = service.run(
        artifact.artifact_id,
        configuration=WesternBlotExtractionConfiguration(
            implementation_name=implementation.identity.implementation_name
        ),
    )

    replay = service.replay_component(first.assembler_invocation_id)

    assert replay.replay_of_invocation_id == first.assembler_invocation_id
    assert replay.invocation_id != first.assembler_invocation_id
    assert replay.prediction_id is not None
    assert replay.publication_id is not None
    assert len(evaluation.list_predictions(first.case_id)) == 2
    detail = pipelines.get_run_detail(first.run_id)
    assert len(detail.invocations) == 4
    assert len(detail.publications) == 2


def test_invalid_model_output_is_a_failed_invocation_not_a_prediction() -> None:
    service, evaluation, pipelines, implementation, artifact = _service(invalid_output=True)

    with pytest.raises(ExtractionOutputInvalid):
        service.run(
            artifact.artifact_id,
            configuration=WesternBlotExtractionConfiguration(
                implementation_name=implementation.identity.implementation_name
            ),
        )

    case = evaluation.get_case_by_key(f"western-blot:{artifact.artifact_id}")
    assert case is not None
    detail = pipelines.list_runs(case.case_id)[0]
    detector, model, assembler = detail.invocations
    assert detector.status is ComponentInvocationStatus.SUCCEEDED
    assert model.status is ComponentInvocationStatus.FAILED
    assert model.result is not None
    assert model.result.failure_kind is InvocationFailureKind.OUTPUT_VALIDATION
    assert assembler.status is ComponentInvocationStatus.PENDING
    assert evaluation.list_predictions(case.case_id) == ()


def test_missing_provider_cost_is_recorded_as_a_warning() -> None:
    service, evaluation, pipelines, implementation, artifact = _service(report_cost=False)

    result = service.run(
        artifact.artifact_id,
        configuration=WesternBlotExtractionConfiguration(
            implementation_name=implementation.identity.implementation_name
        ),
    )

    detail = pipelines.get_run_detail(result.run_id)
    model_invocation = detail.invocations[1]
    assert model_invocation.result is not None
    assert model_invocation.result.cost_microusd == 0
    assert [issue.code for issue in model_invocation.result.validation_issues] == [
        "model_cost_unavailable"
    ]
    assert evaluation.get_prediction(result.prediction_id).cost_microusd == 0


def _service(*, invalid_output: bool = False, report_cost: bool = True):
    content = b"fixture stored scientific source"
    artifact = extraction_artifact(content=content)
    reader = InMemoryExtractionArtifactReader(artifact, content)
    evaluation = EvaluationService(InMemoryEvaluationRepository())
    pipelines = PipelineRegistryService(
        InMemoryPipelineRunRepository(),
        evaluation,
        reader,
    )
    implementation = FixtureExtractionImplementation(
        invalid_output=invalid_output,
        report_cost=report_cost,
    )
    service = WesternBlotExtractionService(
        reader,
        evaluation,
        pipelines,
        ExtractionImplementationRegistry(
            {implementation.identity.implementation_name: implementation}
        ),
    )
    return service, evaluation, pipelines, implementation, artifact
