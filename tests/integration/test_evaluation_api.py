from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import CaseArtifactRole, CaseSourceArtifact
from hiveblot_evaluation import EvaluationService

from apps.api.evaluation_dependencies import get_evaluation_service
from apps.api.main import app
from tests.fakes.evaluation import InMemoryEvaluationRepository


@pytest.mark.asyncio
async def test_annotation_http_boundary_rejects_stale_revisions_and_unknown_fields() -> None:
    service = EvaluationService(InMemoryEvaluationRepository())
    case = service.create_case(
        case_key="http-conflict",
        dataset_id=None,
        source_artifacts=(CaseSourceArtifact(artifact_id=uuid4(), role=CaseArtifactRole.FIGURE),),
    )
    app.dependency_overrides[get_evaluation_service] = lambda: service
    reviewer_id = str(uuid4())
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/v1/evaluation-cases/{case.case_id}/annotations",
                json={"reviewer_id": reviewer_id, "rationale": "initial review"},
            )
            assert created.status_code == 201, created.text
            mutation = created.json()
            assert mutation["schema_version"] == "1.0"
            url = f"/api/v1/annotations/{mutation['annotation']['annotation_id']}/revisions"
            payload = {
                "reviewer_id": reviewer_id,
                "expected_head_revision_id": mutation["revision"]["revision_id"],
                "rationale": "corrected review",
            }
            revised = await client.post(url, json=payload)
            stale = await client.post(url, json=payload)
            invalid = await client.post(url, json={**payload, "unexpected": True})

        assert revised.status_code == 201, revised.text
        assert stale.status_code == 409
        assert invalid.status_code == 422
    finally:
        app.dependency_overrides.pop(get_evaluation_service, None)
