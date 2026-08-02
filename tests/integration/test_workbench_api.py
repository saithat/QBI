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
)
from hiveblot_evaluation import EvaluationService, EvidenceWorkbenchService

from apps.api.main import app
from apps.api.workbench_dependencies import get_workbench_service
from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.workbench import InMemoryArtifactLookup, InMemorySourceContextRepository

NOW = datetime(2026, 8, 2, 19, tzinfo=UTC)


@pytest.mark.asyncio
async def test_workbench_api_exposes_sources_context_and_browser_assets() -> None:
    artifact = ArtifactRecord(
        artifact_id=uuid4(),
        sha256="b" * 64,
        media_type="application/pdf",
        byte_size=4096,
        original_filename="paper.pdf",
        source_uri="https://repository.example/paper.pdf",
        acquisition_method=ArtifactAcquisitionMethod.SOURCE_ADAPTER,
        visibility=ArtifactVisibility.PUBLIC,
        created_at=NOW,
    )
    evaluation = EvaluationService(InMemoryEvaluationRepository(), clock=lambda: NOW)
    case = evaluation.create_case(
        case_key="paper:page-4",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact.artifact_id,
                role=CaseArtifactRole.SOURCE_DOCUMENT,
                page_number=4,
            ),
        ),
    )
    service = EvidenceWorkbenchService(
        evaluation,
        InMemoryArtifactLookup((artifact,)),
        InMemorySourceContextRepository(),
    )
    app.dependency_overrides[get_workbench_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            context = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/sources/"
                f"{artifact.artifact_id}/source_document/context",
                json={
                    "schema_version": "1.0",
                    "caption": "Figure caption",
                    "nearby_text": "Nearby methods text",
                },
            )
            context_revision_id = context.json()["context_revision_id"]
            corrected = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/sources/"
                f"{artifact.artifact_id}/source_document/context",
                json={
                    "schema_version": "1.0",
                    "expected_head_revision_id": context_revision_id,
                    "caption": "Corrected figure caption",
                    "nearby_text": "Nearby methods text",
                },
            )
            stale = await client.put(
                f"/api/v1/evaluation-cases/{case.case_id}/sources/"
                f"{artifact.artifact_id}/source_document/context",
                json={
                    "schema_version": "1.0",
                    "expected_head_revision_id": context_revision_id,
                    "caption": "Stale caption",
                },
            )
            history = await client.get(
                f"/api/v1/evaluation-cases/{case.case_id}/sources/"
                f"{artifact.artifact_id}/source_document/context-revisions"
            )
            workbench = await client.get(f"/api/v1/evaluation-cases/{case.case_id}/workbench")
            page = await client.get(f"/workbench/{case.case_id}")
            script = await client.get("/review/assets/workbench.js")
            missing = await client.get(f"/api/v1/evaluation-cases/{uuid4()}/workbench")

        assert context.status_code == 200
        assert corrected.status_code == 200
        assert stale.status_code == 409
        assert history.status_code == 200
        assert [item["caption"] for item in history.json()["revisions"]] == [
            "Figure caption",
            "Corrected figure caption",
        ]
        assert workbench.status_code == 200
        assert workbench.json()["sources"][0]["artifact"]["sha256"] == "b" * 64
        assert workbench.json()["sources"][0]["caption"] == "Corrected figure caption"
        assert workbench.json()["sources"][0]["page_number"] == 4
        assert page.status_code == 200
        assert "Source evidence" in page.text
        assert script.status_code == 200
        assert "requestFullscreen" in script.text
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.pop(get_workbench_service, None)
