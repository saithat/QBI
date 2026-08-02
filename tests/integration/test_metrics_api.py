from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from hiveblot_contracts import EvaluationScoringInput
from hiveblot_evaluation import EvaluationMetricsService

from apps.api.main import app
from apps.api.metrics_dependencies import get_evaluation_metrics_service
from tests.fakes.metrics import CASE_ONE, CASE_TWO, scoring_input
from tests.fakes.metrics_repository import InMemoryEvaluationMetricRunRepository

NOW = datetime(2026, 8, 2, 23, 30, tzinfo=UTC)
BASELINE_RUN_ID = UUID("60000000-0000-0000-0000-000000000001")
CANDIDATE_RUN_ID = UUID("60000000-0000-0000-0000-000000000002")
COMPARISON_ID = UUID("60000000-0000-0000-0000-000000000003")


@pytest.mark.asyncio
async def test_metrics_api_scores_filters_calibrates_and_compares() -> None:
    identities = iter((BASELINE_RUN_ID, CANDIDATE_RUN_ID, COMPARISON_ID))
    service = EvaluationMetricsService(
        InMemoryEvaluationMetricRunRepository(),
        clock=lambda: NOW,
        identity=lambda: next(identities),
    )
    app.dependency_overrides[get_evaluation_metrics_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            baseline_response = await client.post(
                "/api/v1/evaluation-metric-runs",
                json=_request(scoring_input()),
            )
            candidate_response = await client.post(
                "/api/v1/evaluation-metric-runs",
                json=_request(scoring_input(candidate=True)),
            )
            filtered_response = await client.get(
                "/api/v1/evaluation-metric-runs",
                params={"pipeline_version": "2.0"},
            )
            calibration_response = await client.get(
                f"/api/v1/evaluation-metric-runs/{CANDIDATE_RUN_ID}/calibration",
                params={"category": "field"},
            )
            case_response = await client.get(
                f"/api/v1/evaluation-metric-runs/{CANDIDATE_RUN_ID}/cases/{CASE_TWO}"
            )
            comparison_response = await client.post(
                "/api/v1/evaluation-pipeline-comparisons",
                json={
                    "schema_version": "1.0",
                    "baseline_metric_run_id": str(BASELINE_RUN_ID),
                    "candidate_metric_run_id": str(CANDIDATE_RUN_ID),
                    "minimum_delta": 0.01,
                },
            )
            missing_response = await client.get(
                "/api/v1/evaluation-metric-runs/70000000-0000-0000-0000-000000000001"
            )
            dashboard_response = await client.get("/metrics")
            script_response = await client.get("/review/assets/metrics.js")
    finally:
        app.dependency_overrides.pop(get_evaluation_metrics_service, None)

    assert baseline_response.status_code == 201, baseline_response.text
    assert candidate_response.status_code == 201, candidate_response.text
    baseline = baseline_response.json()
    candidate = candidate_response.json()
    assert baseline["metric_run_id"] == str(BASELINE_RUN_ID)
    assert candidate["metric_run_id"] == str(CANDIDATE_RUN_ID)
    assert candidate["pipeline_version"] == "2.0"
    assert baseline["overall"]["fields"]["normalized"]["f1"] == 1.0
    assert len(candidate["groups"]) > 1

    assert filtered_response.status_code == 200
    assert [item["pipeline_version"] for item in filtered_response.json()["runs"]] == ["2.0"]
    assert calibration_response.status_code == 200
    assert calibration_response.json()["category"] == "field"
    assert calibration_response.json()["calibration"]["observations"] == 2
    assert case_response.status_code == 200
    assert case_response.json()["case_id"] == str(CASE_TWO)
    assert case_response.json()["workbench_url"] == f"/workbench/{CASE_TWO}"
    assert case_response.json()["confidence_outcomes"]

    assert comparison_response.status_code == 201
    comparison = comparison_response.json()
    assert comparison["comparison_id"] == str(COMPARISON_ID)
    assert comparison["overall_delta"] < 0
    assert comparison["regression_case_urls"] == [f"/workbench/{CASE_TWO}"]
    assert {item["case_id"] for item in comparison["cases"]} == {
        str(CASE_ONE),
        str(CASE_TWO),
    }
    assert missing_response.status_code == 404
    assert dashboard_response.status_code == 200
    assert "Pipeline comparison" in dashboard_response.text
    assert "Reliability" in dashboard_response.text
    assert script_response.status_code == 200
    assert "evaluation-pipeline-comparisons" in script_response.text
    assert "regression" in script_response.text


def _request(value: EvaluationScoringInput) -> dict[str, Any]:
    payload = _without_schema_versions(value.model_dump(mode="json"))
    assert isinstance(payload, dict)
    submission = payload["submission"]
    scorer = payload["scorer"]
    assert isinstance(submission, dict)
    assert isinstance(scorer, dict)
    model_versions = submission["model_versions"]
    assert isinstance(model_versions, list)
    for model_version in model_versions:
        assert isinstance(model_version, dict)
        model_version.pop("kind", None)
    pipeline = submission.pop("pipeline")
    assert isinstance(pipeline, dict)
    submission["pipeline_name"] = pipeline["name"]
    submission["pipeline_version"] = pipeline["version"]
    payload["scorer_name"] = scorer["name"]
    payload["scorer_version"] = scorer["version"]
    del payload["scorer"]
    payload["schema_version"] = "1.0"
    return payload


def _without_schema_versions(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_schema_versions(item)
            for key, item in value.items()
            if key != "schema_version"
        }
    if isinstance(value, list):
        return [_without_schema_versions(item) for item in value]
    return value
