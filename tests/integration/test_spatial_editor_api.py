from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import (
    CaseArtifactRole,
    CaseSourceArtifact,
    ModelIdentifier,
    PredictionEvidence,
)
from hiveblot_evaluation import EvaluationService, SpatialAnnotationService

from apps.api.evaluation_dependencies import get_evaluation_service
from apps.api.main import app
from apps.api.spatial_editor_dependencies import get_spatial_annotation_service
from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.spatial import spatial_annotation_set


@pytest.mark.asyncio
async def test_spatial_editor_accepts_geometry_appends_history_and_rejects_bounds() -> None:
    evaluation = EvaluationService(
        InMemoryEvaluationRepository(),
        clock=lambda: datetime(2026, 8, 2, 19, tzinfo=UTC),
    )
    spatial = SpatialAnnotationService(evaluation)
    app.dependency_overrides[get_evaluation_service] = lambda: evaluation
    app.dependency_overrides[get_spatial_annotation_service] = lambda: spatial
    artifact_id = uuid4()
    reviewer_id = uuid4()
    case = evaluation.create_case(
        case_key=f"spatial-api:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact_id,
                role=CaseArtifactRole.FIGURE,
                page_number=1,
            ),
        ),
    )
    complete = spatial_annotation_set(artifact_id)
    predicted_regions = complete.spatial_annotations[3:5]
    prediction = evaluation.add_prediction(
        case.case_id,
        prediction_schema="western-blot-spatial",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(provider="fixture", name="detector", version="1"),
        pipeline=None,
        raw_output_json="raw regions",
        normalized_output_json="{}",
        configuration_json="{}",
        evidence=tuple(
            PredictionEvidence(
                artifact_id=artifact_id,
                field_path=f"/lanes/{index}",
                region_id=annotation.region.region_id,
                region=annotation.region,
                description=annotation.label or "lane",
            )
            for index, annotation in enumerate(predicted_regions)
        ),
        validation_issues=(),
        confidence=0.9,
        trace_id=uuid4(),
        latency_ms=4,
        cost_microusd=0,
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            editor = await client.get(
                f"/api/v1/evaluation-cases/{case.case_id}/spatial-editor",
                params={"reviewer_id": str(reviewer_id)},
            )
            page = await client.get(f"/spatial/{case.case_id}")
            script = await client.get("/review/assets/spatial.js")
            accepted = await client.post(
                f"/api/v1/evaluation-cases/{case.case_id}/"
                "spatial-annotation-drafts/accept-prediction",
                json={
                    "schema_version": "1.0",
                    "prediction_id": str(prediction.prediction_id),
                    "accept_all": True,
                },
            )
            created = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/spatial-annotations",
                json=_save_payload(reviewer_id, complete),
            )
            first_revision_id = created.json()["revision"]["revision_id"]
            corrected = complete.model_dump(mode="json")
            corrected["spatial_annotations"][7]["region"]["x"] += 2
            revised = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/spatial-annotations",
                json=_save_payload(
                    reviewer_id,
                    corrected,
                    expected_head_revision_id=first_revision_id,
                ),
            )
            second_revision_id = revised.json()["revision"]["revision_id"]
            stale = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/spatial-annotations",
                json=_save_payload(
                    reviewer_id,
                    complete,
                    expected_head_revision_id=first_revision_id,
                ),
            )
            annotation_id = revised.json()["annotation"]["annotation_id"]
            undone = await client.post(
                f"/api/v1/annotations/{annotation_id}/spatial-undo",
                json={
                    "schema_version": "1.0",
                    "reviewer_id": str(reviewer_id),
                    "expected_head_revision_id": second_revision_id,
                    "target_revision_id": first_revision_id,
                },
            )
            history = await client.get(f"/api/v1/annotations/{annotation_id}/revisions")
            invalid = complete.model_dump(mode="json")
            invalid["spatial_annotations"][0]["region"]["x"] = 1
            invalid["spatial_annotations"][0]["region"]["width"] = 400
            rejected = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/spatial-annotations",
                json=_save_payload(
                    reviewer_id,
                    invalid,
                    expected_head_revision_id=undone.json()["revision"]["revision_id"],
                ),
            )

        assert editor.status_code == 200
        assert editor.json()["selected_prediction_id"] == str(prediction.prediction_id)
        assert editor.json()["comparison"]["removed_count"] == 2
        assert page.status_code == 200
        assert "Spatial editor" in page.text
        assert script.status_code == 200
        assert "source_pixels" in script.text
        assert "setPointerCapture" in script.text
        assert accepted.status_code == 200
        assert len(accepted.json()["annotation_set"]["spatial_annotations"]) == 2
        assert created.status_code == 201
        assert len(created.json()["revision"]["spatial_annotations"]) == 9
        assert revised.status_code == 201
        assert revised.json()["revision"]["revision_number"] == 2
        assert stale.status_code == 409
        assert undone.status_code == 201
        assert undone.json()["revision"]["revision_number"] == 3
        assert history.status_code == 200
        assert len(history.json()["revisions"]) == 3
        assert rejected.status_code == 422
    finally:
        app.dependency_overrides.pop(get_evaluation_service, None)
        app.dependency_overrides.pop(get_spatial_annotation_service, None)


def _save_payload(
    reviewer_id,
    annotation_set,
    *,
    expected_head_revision_id=None,
):
    payload = {
        "schema_version": "1.0",
        "reviewer_id": str(reviewer_id),
        "annotation_set": (
            annotation_set.model_dump(mode="json")
            if hasattr(annotation_set, "model_dump")
            else annotation_set
        ),
        "rationale": "Spatial editor autosave",
        "error_codes": [],
    }
    if expected_head_revision_id is not None:
        payload["expected_head_revision_id"] = expected_head_revision_id
    return payload
