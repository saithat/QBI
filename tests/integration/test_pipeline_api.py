from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceArtifact,
    SpatialAnnotationSet,
)
from hiveblot_evaluation import EvaluationService, PipelineRegistryService

from apps.api.main import app
from apps.api.pipeline_dependencies import get_pipeline_registry_service
from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.pipeline_registry import InMemoryPipelineRunRepository
from tests.fakes.structured import structured_annotation
from tests.fakes.workbench import InMemoryArtifactLookup

NOW = datetime(2026, 8, 2, 22, tzinfo=UTC)


@pytest.mark.asyncio
async def test_pipeline_api_preserves_history_replays_and_publishes_explicitly() -> None:
    source = _artifact("a", "source.png")
    output = _artifact("b", "normalized.json", media_type="application/json")
    evaluation = EvaluationService(InMemoryEvaluationRepository(), clock=lambda: NOW)
    case = evaluation.create_case(
        case_key=f"pipeline-api:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=source.artifact_id,
                role=CaseArtifactRole.FIGURE,
            ),
        ),
    )
    service = PipelineRegistryService(
        InMemoryPipelineRunRepository(),
        evaluation,
        InMemoryArtifactLookup((source, output)),
        clock=lambda: NOW,
    )
    app.dependency_overrides[get_pipeline_registry_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            definition_response = await client.post(
                "/api/v1/pipeline-definitions",
                json=_definition_request(),
            )
            assert definition_response.status_code == 201
            definition = definition_response.json()

            cyclic_request = _definition_request()
            cyclic_request["pipeline"] = {
                "name": "western-blot-extraction",
                "version": "cyclic",
            }
            components = cyclic_request["components"]
            assert isinstance(components, list)
            first_component = components[0]
            assert isinstance(first_component, dict)
            first_component["depends_on"] = ["normalize_metadata"]
            cyclic_response = await client.post(
                "/api/v1/pipeline-definitions",
                json=cyclic_request,
            )

            run_response = await client.post(
                f"/api/v1/evaluation-cases/{case.case_id}/pipeline-runs",
                json={
                    "schema_version": "1.0",
                    "definition_id": definition["definition_id"],
                    "input_artifact_ids": [str(source.artifact_id)],
                    "configuration_json": '{"mode":"api-test"}',
                    "trace_id": str(uuid4()),
                },
            )
            assert run_response.status_code == 201
            run = run_response.json()
            first, second = run["invocations"]

            first_result = await client.post(
                f"/api/v1/component-invocations/{first['invocation_id']}/results",
                json=_success_request(
                    schema_name="spatial-annotation-set",
                    normalized=SpatialAnnotationSet().model_dump_json(),
                ),
            )
            duplicate_result = await client.post(
                f"/api/v1/component-invocations/{first['invocation_id']}/results",
                json=_success_request(
                    schema_name="spatial-annotation-set",
                    normalized=SpatialAnnotationSet().model_dump_json(),
                ),
            )
            invalid_result = await client.post(
                f"/api/v1/component-invocations/{second['invocation_id']}/results",
                json=_success_request(
                    schema_name="western-blot-structured-annotation",
                    normalized='{"schema_version":"1.0","unknown":true}',
                ),
            )

            replay_response = await client.post(
                f"/api/v1/component-invocations/{second['invocation_id']}/replays",
                json={
                    "schema_version": "1.0",
                    "configuration_json": '{"prompt":"metadata-v2"}',
                    "trace_id": str(uuid4()),
                },
            )
            assert replay_response.status_code == 201
            replay = replay_response.json()
            normalized = structured_annotation("TP53").model_dump_json()
            replay_result = await client.post(
                f"/api/v1/component-invocations/{replay['invocation_id']}/results",
                json=_success_request(
                    schema_name="western-blot-structured-annotation",
                    normalized=normalized,
                    output_artifact_ids=[str(output.artifact_id)],
                ),
            )
            publication_response = await client.post(
                f"/api/v1/pipeline-runs/{run['run']['run_id']}/publications",
                json={
                    "schema_version": "1.0",
                    "output_schema": {
                        "name": "western-blot-structured-annotation",
                        "version": "1.0",
                    },
                    "normalized_output_json": normalized,
                    "values": [
                        {
                            "field_path": "",
                            "producing_invocation_id": replay["invocation_id"],
                            "result_path": "",
                            "value_json": normalized,
                        }
                    ],
                    "output_artifact_ids": [str(output.artifact_id)],
                    "trace_id": str(uuid4()),
                },
            )
            history_response = await client.get(
                f"/api/v1/evaluation-cases/{case.case_id}/pipeline-runs"
            )
            detail_response = await client.get(f"/api/v1/pipeline-runs/{run['run']['run_id']}")

        assert first_result.status_code == 200
        assert cyclic_response.status_code == 422
        assert duplicate_result.status_code == 409
        assert invalid_result.status_code == 200
        assert invalid_result.json()["status"] == "failed"
        assert invalid_result.json()["result"]["failure_kind"] == "output_validation"
        assert invalid_result.json()["result"]["raw_output_json"] == "raw provider output"
        assert replay_result.status_code == 200
        assert replay_result.json()["status"] == "succeeded"
        assert replay["invocation_id"] != second["invocation_id"]
        assert replay["replay_of_invocation_id"] == second["invocation_id"]
        assert publication_response.status_code == 201
        assert (
            publication_response.json()["values"][0]["producing_invocation_id"]
            == replay["invocation_id"]
        )
        assert history_response.status_code == 200
        assert len(history_response.json()["runs"]) == 1
        assert len(detail_response.json()["invocations"]) == 3
        assert detail_response.json()["run"]["status"] == "published"
        assert len(detail_response.json()["publications"]) == 1
    finally:
        app.dependency_overrides.pop(get_pipeline_registry_service, None)


def _definition_request() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "pipeline": {"name": "western-blot-extraction", "version": "1.0"},
        "description": "API test pipeline",
        "components": [
            {
                "component_type": "model",
                "component_key": "detect_regions",
                "output_schema": {"name": "spatial-annotation-set", "version": "1.0"},
                "configuration_json": '{"temperature":0}',
                "provider": "local",
                "model_name": "western-blot-detector",
                "model_version": "1.0",
                "prompt_version": "detect-v1",
            },
            {
                "component_type": "tool",
                "component_key": "normalize_metadata",
                "depends_on": ["detect_regions"],
                "output_schema": {
                    "name": "western-blot-structured-annotation",
                    "version": "1.0",
                },
                "configuration_json": "{}",
                "tool_name": "western-blot-normalizer",
                "tool_version": "1.0",
            },
        ],
        "configuration_json": "{}",
    }


def _success_request(
    *,
    schema_name: str,
    normalized: str,
    output_artifact_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "outcome": "succeeded",
        "output_schema": {"name": schema_name, "version": "1.0"},
        "raw_output_json": "raw provider output",
        "normalized_output_json": normalized,
        "output_artifact_ids": output_artifact_ids or [],
        "evidence": [],
        "validation_issues": [],
        "latency_ms": 4,
        "cost_microusd": 2,
    }


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
