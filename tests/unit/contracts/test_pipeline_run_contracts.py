from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ComponentInvocationRecord,
    ComponentInvocationStatus,
    PipelineDefinitionRecord,
    PipelineIdentifier,
    PipelineRunDetail,
    PipelineRunRecord,
    PipelineRunStatus,
)
from pydantic import ValidationError

from tests.fakes.pipeline import NOW, artifact_reference, pipeline_definition


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
