from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from hiveblot_evaluation import EvaluationService

from apps.api.evaluation_dependencies import get_evaluation_service
from apps.api.main import app
from tests.fakes.evaluation import InMemoryEvaluationRepository


@pytest.mark.asyncio
async def test_annotation_api_appends_history_and_rejects_stale_edits() -> None:
    repository = InMemoryEvaluationRepository()
    service = EvaluationService(
        repository,
        clock=lambda: datetime(2026, 8, 2, 15, tzinfo=UTC),
    )
    app.dependency_overrides[get_evaluation_service] = lambda: service
    artifact_id = uuid4()
    reviewer_id = uuid4()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            create_case = await client.post(
                "/api/v1/evaluation-cases",
                json={
                    "schema_version": "1.0",
                    "case_key": "paper:figure-1:A",
                    "source_artifacts": [
                        {
                            "schema_version": "1.0",
                            "artifact_id": str(artifact_id),
                            "role": "figure",
                            "page_number": 4,
                        }
                    ],
                },
            )
            assert create_case.status_code == 201
            case_id = create_case.json()["case_id"]

            prediction = await client.post(
                f"/api/v1/evaluation-cases/{case_id}/predictions",
                json={
                    "schema_version": "1.0",
                    "prediction_schema": "western-blot-extraction",
                    "prediction_schema_version": "1.0",
                    "producer": {
                        "schema_version": "1.0",
                        "kind": "model",
                        "provider": "local",
                        "name": "Qwen3-VL",
                        "version": "baseline",
                    },
                    "raw_output_json": "raw malformed text remains available",
                    "normalized_output_json": '{"target":"p53"}',
                    "configuration_json": "{}",
                    "evidence": [],
                    "validation_issues": [],
                    "confidence": 0.8,
                    "trace_id": str(uuid4()),
                    "latency_ms": 100,
                    "cost_microusd": 0,
                },
            )
            assert prediction.status_code == 201
            assert prediction.json()["raw_output_json"].startswith("raw malformed")

            first_payload = _annotation_payload(artifact_id, reviewer_id, "p53")
            created = await client.post(
                f"/api/v1/evaluation-cases/{case_id}/annotations",
                json=first_payload,
            )
            assert created.status_code == 201
            annotation_id = created.json()["annotation"]["annotation_id"]
            first_revision_id = created.json()["revision"]["revision_id"]

            second_payload = _annotation_payload(artifact_id, reviewer_id, "TP53")
            second_payload["expected_head_revision_id"] = first_revision_id
            revised = await client.post(
                f"/api/v1/annotations/{annotation_id}/revisions",
                json=second_payload,
            )
            stale = await client.post(
                f"/api/v1/annotations/{annotation_id}/revisions",
                json=second_payload,
            )
            history = await client.get(f"/api/v1/annotations/{annotation_id}/revisions")

            assert revised.status_code == 201
            assert revised.json()["revision"]["revision_number"] == 2
            assert stale.status_code == 409
            assert len(history.json()["revisions"]) == 2
            assert history.json()["revisions"][0]["field_annotations"][0]["value"] == "p53"
            assert history.json()["revisions"][1]["field_annotations"][0]["value"] == "TP53"

            unknown_field = dict(first_payload)
            unknown_field["unexpected"] = "rejected"
            invalid = await client.post(
                f"/api/v1/evaluation-cases/{case_id}/annotations",
                json=unknown_field,
            )
            assert invalid.status_code == 422
    finally:
        app.dependency_overrides.pop(get_evaluation_service, None)


def _annotation_payload(artifact_id, reviewer_id, target):
    spatial_id = uuid4()
    field_id = uuid4()
    return {
        "schema_version": "1.0",
        "reviewer_id": str(reviewer_id),
        "rationale": "reviewed source evidence",
        "error_codes": [],
        "field_annotations": [
            {
                "schema_version": "1.0",
                "field_annotation_id": str(field_id),
                "target": {
                    "schema_version": "1.0",
                    "target_type": "field_path",
                    "field_path": "/targets/0/name",
                },
                "state": "present",
                "value": target,
                "original_extracted_text": "P53",
                "evidence_region_ids": [str(spatial_id)],
            }
        ],
        "spatial_annotations": [
            {
                "schema_version": "1.0",
                "spatial_annotation_id": str(spatial_id),
                "annotation_type": "band",
                "state": "present",
                "region": {
                    "schema_version": "1.0",
                    "region_id": str(spatial_id),
                    "source_artifact_id": str(artifact_id),
                    "coordinate_space": "source_pixels",
                    "x": 10.0,
                    "y": 20.0,
                    "width": 30.0,
                    "height": 12.0,
                    "canvas_width": 200,
                    "canvas_height": 100,
                    "page_number": 4,
                },
                "label": target,
            }
        ],
        "relationships": [
            {
                "schema_version": "1.0",
                "relationship_id": str(uuid4()),
                "subject_id": str(field_id),
                "relation_type": "supported_by",
                "object_id": str(spatial_id),
            }
        ],
    }
