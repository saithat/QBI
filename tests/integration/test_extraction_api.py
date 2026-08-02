from uuid import UUID

from fastapi.testclient import TestClient
from hiveblot_evaluation import EvaluationService, PipelineRegistryService
from hiveblot_extraction import (
    ExtractionImplementationRegistry,
    WesternBlotExtractionService,
)

from apps.api.application import app
from apps.api.extraction_dependencies import get_western_blot_extraction_service
from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.extraction import (
    FixtureExtractionImplementation,
    InMemoryExtractionArtifactReader,
    extraction_artifact,
)
from tests.fakes.pipeline_registry import InMemoryPipelineRunRepository


def test_extraction_api_runs_stored_source_and_selective_replay() -> None:
    content = b"fixture stored scientific source"
    artifact = extraction_artifact(content=content)
    reader = InMemoryExtractionArtifactReader(artifact, content)
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
    app.dependency_overrides[get_western_blot_extraction_service] = lambda: service
    client = TestClient(app)
    try:
        implementations = client.get("/api/v1/western-blot-extraction-implementations")
        assert implementations.status_code == 200
        assert implementations.json()["implementations"][0]["model_version"] == "oduah-page-4"

        invalid = client.post(
            "/api/v1/western-blot-extractions",
            json={
                "schema_version": "1.0",
                "source_artifact_id": str(artifact.artifact_id),
                "implementation_name": implementation.identity.implementation_name,
                "future_field": True,
            },
        )
        assert invalid.status_code == 422

        started = client.post(
            "/api/v1/western-blot-extractions",
            json={
                "schema_version": "1.0",
                "source_artifact_id": str(artifact.artifact_id),
                "implementation_name": implementation.identity.implementation_name,
            },
        )
        assert started.status_code == 201, started.text
        payload = started.json()
        assert payload["candidate_count"] == 1
        assert payload["relevant_candidate_count"] == 1
        assert payload["panel_count"] == 3
        assert payload["lane_count"] == 20
        assert payload["warning_count"] >= 1

        replayed = client.post(
            "/api/v1/western-blot-extraction-invocations/"
            f"{payload['assembler_invocation_id']}/replays",
            json={"schema_version": "1.0"},
        )
        assert replayed.status_code == 201, replayed.text
        assert replayed.json()["replay_of_invocation_id"] == payload["assembler_invocation_id"]
        assert replayed.json()["prediction_id"] != payload["prediction_id"]
        assert len(evaluation.list_predictions(UUID(payload["case_id"]))) == 2
    finally:
        app.dependency_overrides.clear()
