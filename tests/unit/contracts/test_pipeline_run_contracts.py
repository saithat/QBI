from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ComponentInvocationRecord,
    ComponentInvocationStatus,
    ModelPipelineComponent,
    OutputSchemaIdentifier,
    PipelineDefinitionRecord,
    PipelineIdentifier,
    PipelineRunDetail,
    PipelineRunRecord,
    PipelineRunStatus,
    SucceededComponentResult,
)
from pydantic import ValidationError

from tests.fakes.pipeline import NOW, artifact_reference, pipeline_definition


def test_pipeline_definition_round_trips_a_versioned_component_dag() -> None:
    definition = pipeline_definition()

    restored = PipelineDefinitionRecord.model_validate_json(definition.model_dump_json())

    assert restored == definition
    assert restored.components[0].component_type == "model"
    assert restored.components[1].depends_on == ("detect_regions",)


def test_pipeline_definition_rejects_unknown_dependencies_and_cycles() -> None:
    definition = pipeline_definition()
    unknown = definition.components[0].model_copy(update={"depends_on": ("missing",)})
    with pytest.raises(ValidationError, match="must reference"):
        PipelineDefinitionRecord(
            definition_id=uuid4(),
            pipeline=PipelineIdentifier(name="invalid", version="1"),
            components=(unknown,),
            created_at=NOW,
        )

    first = definition.components[0].model_copy(update={"depends_on": ("normalize_metadata",)})
    with pytest.raises(ValidationError, match="cannot contain a cycle"):
        PipelineDefinitionRecord(
            definition_id=uuid4(),
            pipeline=PipelineIdentifier(name="cyclic", version="1"),
            components=(first, definition.components[1]),
            created_at=NOW,
        )


def test_invocation_status_must_match_its_discriminated_result() -> None:
    definition = pipeline_definition()
    result = SucceededComponentResult(
        output_schema=OutputSchemaIdentifier(name="spatial-annotation-set", version="1.0"),
        raw_output_json="raw",
        normalized_output_json='{"schema_version":"1.0"}',
        latency_ms=10,
        cost_microusd=2,
        completed_at=NOW,
    )

    with pytest.raises(ValidationError, match="status and result must agree"):
        ComponentInvocationRecord(
            invocation_id=uuid4(),
            run_id=uuid4(),
            component=definition.components[0],
            status=ComponentInvocationStatus.FAILED,
            input_artifacts=(artifact_reference(),),
            configuration_json="{}",
            trace_id=uuid4(),
            result=result,
            created_at=NOW,
        )


def test_pipeline_run_detail_rejects_foreign_invocation_parents() -> None:
    definition = pipeline_definition()
    artifact = artifact_reference()
    run = PipelineRunRecord(
        run_id=uuid4(),
        case_id=uuid4(),
        definition_id=definition.definition_id,
        pipeline=definition.pipeline,
        status=PipelineRunStatus.ACTIVE,
        input_artifacts=(artifact,),
        configuration_json="{}",
        trace_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    invocation = ComponentInvocationRecord(
        invocation_id=uuid4(),
        run_id=run.run_id,
        component=definition.components[0],
        status=ComponentInvocationStatus.PENDING,
        input_artifacts=(artifact,),
        parent_invocation_ids=(uuid4(),),
        configuration_json="{}",
        trace_id=run.trace_id,
        created_at=NOW,
    )

    with pytest.raises(ValidationError, match="parents must belong"):
        PipelineRunDetail(run=run, invocations=(invocation,), publications=())

    replay = invocation.model_copy(
        update={
            "parent_invocation_ids": (),
            "replay_of_invocation_id": uuid4(),
        }
    )
    with pytest.raises(ValidationError, match="replay sources must belong"):
        PipelineRunDetail(run=run, invocations=(replay,), publications=())


def test_component_configuration_rejects_non_object_json() -> None:
    definition = pipeline_definition()
    component = definition.components[0]
    assert isinstance(component, ModelPipelineComponent)

    with pytest.raises(ValidationError, match="must contain a JSON object"):
        ModelPipelineComponent.model_validate(
            component.model_copy(update={"configuration_json": "[]"}).model_dump(mode="python")
        )
