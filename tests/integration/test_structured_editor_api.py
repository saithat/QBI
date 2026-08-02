from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import CaseArtifactRole, CaseSourceArtifact, ModelIdentifier
from hiveblot_evaluation import EvaluationService, StructuredAnnotationService

from apps.api.evaluation_dependencies import get_evaluation_service
from apps.api.main import app
from apps.api.structured_editor_dependencies import get_structured_annotation_service
from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.structured import structured_annotation


@pytest.mark.asyncio
async def test_structured_editor_accepts_predictions_autosaves_and_undoes_by_revision() -> None:
    evaluation = EvaluationService(
        InMemoryEvaluationRepository(),
        clock=lambda: datetime(2026, 8, 2, 17, tzinfo=UTC),
    )
    structured = StructuredAnnotationService(evaluation)
    app.dependency_overrides[get_evaluation_service] = lambda: evaluation
    app.dependency_overrides[get_structured_annotation_service] = lambda: structured
    reviewer_id = uuid4()
    case = evaluation.create_case(
        case_key=f"structured-api:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=uuid4(),
                role=CaseArtifactRole.FIGURE,
                page_number=1,
            ),
        ),
    )
    predicted = structured_annotation(reviewer_notes=None)
    prediction = evaluation.add_prediction(
        case.case_id,
        prediction_schema="western-blot-structured-annotation",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(provider="fixture", name="extractor", version="1"),
        pipeline=None,
        raw_output_json="raw model output",
        normalized_output_json=predicted.model_dump_json(),
        configuration_json="{}",
        evidence=(),
        validation_issues=(),
        confidence=0.94,
        trace_id=uuid4(),
        latency_ms=3,
        cost_microusd=0,
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            editor = await client.get(
                f"/api/v1/evaluation-cases/{case.case_id}/structured-editor",
                params={"reviewer_id": str(reviewer_id)},
            )
            accepted = await client.post(
                f"/api/v1/evaluation-cases/{case.case_id}/"
                "structured-annotation-drafts/accept-prediction",
                json={
                    "schema_version": "1.0",
                    "prediction_id": str(prediction.prediction_id),
                    "accepted_entity_ids": [str(predicted.proteins[0].entity_id)],
                },
            )
            created = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/structured-annotations",
                json=_save_payload(reviewer_id, predicted),
            )
            first_revision_id = created.json()["revision"]["revision_id"]
            corrected_payload = predicted.model_dump(mode="json")
            corrected_payload["proteins"][0]["name"] = "p53"
            revised = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/structured-annotations",
                json=_save_payload(
                    reviewer_id,
                    corrected_payload,
                    expected_head_revision_id=first_revision_id,
                ),
            )
            second_revision_id = revised.json()["revision"]["revision_id"]
            stale = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/structured-annotations",
                json=_save_payload(
                    reviewer_id,
                    predicted,
                    expected_head_revision_id=first_revision_id,
                ),
            )
            annotation_id = revised.json()["annotation"]["annotation_id"]
            undone = await client.post(
                f"/api/v1/annotations/{annotation_id}/structured-undo",
                json={
                    "schema_version": "1.0",
                    "reviewer_id": str(reviewer_id),
                    "expected_head_revision_id": second_revision_id,
                    "target_revision_id": first_revision_id,
                },
            )
            history = await client.get(f"/api/v1/annotations/{annotation_id}/revisions")
            canonical = await client.get(
                "/api/v1/canonical-entities",
                params={"query": "p53", "entity_type": "protein"},
            )
            page = await client.get(f"/annotate/{case.case_id}")
            script = await client.get("/review/assets/annotate.js")
            invalid_payload = predicted.model_dump(mode="json")
            invalid_payload["proteins"][0]["unknown"] = "rejected"
            invalid = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/structured-annotations",
                json=_save_payload(
                    reviewer_id,
                    invalid_payload,
                    expected_head_revision_id=undone.json()["revision"]["revision_id"],
                ),
            )

        assert editor.status_code == 200
        assert editor.json()["selected_prediction_id"] == str(prediction.prediction_id)
        assert editor.json()["comparison"]["removed_count"] > 0
        assert accepted.status_code == 200
        assert len(accepted.json()["annotation"]["proteins"]) == 1
        assert created.status_code == 201
        assert created.json()["revision"]["structured_annotation"]["proteins"][0]["name"] == "TP53"
        assert revised.status_code == 201
        assert revised.json()["revision"]["revision_number"] == 2
        assert stale.status_code == 409
        assert undone.status_code == 201
        assert undone.json()["revision"]["revision_number"] == 3
        assert undone.json()["revision"]["structured_annotation"]["proteins"][0]["name"] == "TP53"
        assert history.status_code == 200
        assert len(history.json()["revisions"]) == 3
        assert canonical.status_code == 200
        assert canonical.json()["candidates"][0]["canonical_id"] == "uniprot:P04637"
        assert page.status_code == 200
        assert "Protein targets &amp; loading controls" in page.text
        assert script.status_code == 200
        assert "structured-undo" in script.text
        assert invalid.status_code == 422
    finally:
        app.dependency_overrides.pop(get_evaluation_service, None)
        app.dependency_overrides.pop(get_structured_annotation_service, None)


def _save_payload(
    reviewer_id,
    annotation,
    *,
    expected_head_revision_id=None,
):
    payload = {
        "schema_version": "1.0",
        "reviewer_id": str(reviewer_id),
        "annotation": (
            annotation.model_dump(mode="json") if hasattr(annotation, "model_dump") else annotation
        ),
        "rationale": "Autosaved structured review",
        "error_codes": [],
    }
    if expected_head_revision_id is not None:
        payload["expected_head_revision_id"] = expected_head_revision_id
    return payload
