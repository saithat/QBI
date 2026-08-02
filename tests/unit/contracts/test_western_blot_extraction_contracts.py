from uuid import UUID

import pytest
from hiveblot_contracts import (
    WesternBlotExtractionConfiguration,
    WesternBlotExtractionRunRecord,
)
from hiveblot_evaluation import EvaluationService, PipelineRegistryService
from hiveblot_extraction import (
    ExtractionImplementationRegistry,
    WesternBlotExtractionService,
)
from pydantic import ValidationError

from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.extraction import (
    FixtureExtractionImplementation,
    InMemoryExtractionArtifactReader,
    extraction_artifact,
)
from tests.fakes.pipeline_registry import InMemoryPipelineRunRepository


def test_extraction_configuration_is_strict_frozen_and_versioned() -> None:
    value = WesternBlotExtractionConfiguration(
        implementation_name="fixture-western-blot",
        minimum_candidate_score=0.35,
        minimum_model_score=0.65,
    )

    assert value.schema_version == "1.0"
    with pytest.raises(ValidationError):
        value.dpi = 72
    with pytest.raises(ValidationError):
        WesternBlotExtractionConfiguration.model_validate(
            {
                **value.model_dump(mode="python"),
                "future_field": True,
            }
        )
    with pytest.raises(ValidationError):
        WesternBlotExtractionConfiguration.model_validate(
            {
                **value.model_dump(mode="python"),
                "maximum_candidates": "50",
            }
        )


def test_run_contract_rejects_cross_case_result_identity() -> None:
    artifact = extraction_artifact()
    reader = InMemoryExtractionArtifactReader(artifact, b"fixture stored scientific source")
    evaluation = EvaluationService(InMemoryEvaluationRepository())
    pipelines = PipelineRegistryService(
        InMemoryPipelineRunRepository(),
        evaluation,
        reader,
    )
    implementation = FixtureExtractionImplementation()
    service = WesternBlotExtractionService(
        reader,
        evaluation,
        pipelines,
        ExtractionImplementationRegistry(
            {implementation.identity.implementation_name: implementation}
        ),
    )
    record = service.run(
        artifact.artifact_id,
        configuration=WesternBlotExtractionConfiguration(
            implementation_name=implementation.identity.implementation_name
        ),
    )
    payload = record.model_dump(mode="python")
    payload["case_id"] = UUID("00000000-0000-0000-0000-000000000001")

    with pytest.raises(ValidationError, match="case IDs"):
        WesternBlotExtractionRunRecord.model_validate(payload)
