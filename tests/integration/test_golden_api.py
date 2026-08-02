from uuid import uuid4

import httpx
import pytest
from hiveblot_evaluation import GoldenDatasetExportPublisher, GoldenDatasetService

from apps.api.golden_dependencies import get_golden_dataset_service
from apps.api.main import app
from tests.fakes.golden import InMemoryDatasetExportStore, InMemoryGoldenDatasetRepository
from tests.fakes.golden_fixture import (
    create_reviewed_case,
    evaluation_fixture,
    public_artifact,
)
from tests.fakes.workbench import InMemoryArtifactLookup


@pytest.mark.asyncio
async def test_golden_api_promotes_freezes_exports_and_preserves_history() -> None:
    evaluation, _, clock = evaluation_fixture()
    fixture = create_reviewed_case(
        evaluation,
        public_artifact("f"),
        case_key="golden-api:paper-f:figure-1",
    )
    export_store = InMemoryDatasetExportStore()
    service = GoldenDatasetService(
        InMemoryGoldenDatasetRepository(),
        evaluation,
        InMemoryArtifactLookup((fixture.artifact,)),
        GoldenDatasetExportPublisher(export_store),
        clock=clock,
    )
    app.dependency_overrides[get_golden_dataset_service] = lambda: service
    actor_id = uuid4()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            create_response = await client.post(
                "/api/v1/golden-datasets",
                json={
                    "schema_version": "1.0",
                    "dataset_name": "api-golden",
                    "dataset_version": "1.0",
                    "dataset_type": "frozen_test",
                    "created_by": str(actor_id),
                },
            )
            assert create_response.status_code == 201, create_response.text
            dataset = create_response.json()
            dataset_id = dataset["dataset_id"]
            add_response = await client.post(
                f"/api/v1/golden-datasets/{dataset_id}/cases/{fixture.case_id}",
                json={
                    "schema_version": "1.0",
                    "expected_dataset_revision": 1,
                    "paper_key": "doi:paper-f",
                    "split": "test",
                    "actor_id": str(actor_id),
                    "rationale": "API golden case.",
                },
            )
            assert add_response.status_code == 201, add_response.text
            detail = add_response.json()
            stale_response = await client.post(
                f"/api/v1/golden-datasets/{dataset_id}/cases/{fixture.case_id}/promotions",
                json=_promotion(
                    dataset_revision=1,
                    state_version=1,
                    target="predicted",
                    actor_id=actor_id,
                ),
            )
            assert stale_response.status_code == 409
            for target in ("predicted", "reviewed", "gold_candidate", "gold"):
                member = detail["members"][0]
                response = await client.post(
                    f"/api/v1/golden-datasets/{dataset_id}/cases/{fixture.case_id}/promotions",
                    json=_promotion(
                        dataset_revision=detail["dataset"]["revision"],
                        state_version=member["state_version"],
                        target=target,
                        actor_id=actor_id,
                        selected_revision_id=(
                            fixture.selected_revision_id if target == "reviewed" else None
                        ),
                    ),
                )
                assert response.status_code == 200, response.text
                detail = response.json()
            transitions_response = await client.get(
                f"/api/v1/golden-datasets/{dataset_id}/cases/{fixture.case_id}/transitions"
            )
            freeze_response = await client.post(
                f"/api/v1/golden-datasets/{dataset_id}/snapshots",
                json={
                    "schema_version": "1.0",
                    "expected_dataset_revision": detail["dataset"]["revision"],
                    "frozen_by": str(actor_id),
                },
            )
            assert freeze_response.status_code == 201, freeze_response.text
            snapshot = freeze_response.json()
            snapshot_id = snapshot["snapshot_id"]
            export_response = await client.post(
                f"/api/v1/golden-dataset-snapshots/{snapshot_id}/exports",
                json={"schema_version": "1.0"},
            )
            repeated_export = await client.post(
                f"/api/v1/golden-dataset-snapshots/{snapshot_id}/exports",
                json={"schema_version": "1.0"},
            )
            download_response = await client.post(
                f"/api/v1/golden-dataset-snapshots/{snapshot_id}/exports/manifest_json/download-url"
            )
            history_response = await client.get("/api/v1/golden-datasets")
            detail_response = await client.get(f"/api/v1/golden-datasets/{dataset_id}")
            snapshot_response = await client.get(f"/api/v1/golden-dataset-snapshots/{snapshot_id}")
            frozen_delete = await client.request(
                "DELETE",
                f"/api/v1/golden-datasets/{dataset_id}",
                json={
                    "schema_version": "1.0",
                    "expected_dataset_revision": detail_response.json()["dataset"]["revision"],
                },
            )
    finally:
        app.dependency_overrides.pop(get_golden_dataset_service, None)

    assert transitions_response.status_code == 200
    assert [item["to_state"] for item in transitions_response.json()["transitions"]] == [
        "unlabeled",
        "predicted",
        "reviewed",
        "gold_candidate",
        "gold",
    ]
    assert snapshot["cases"][0]["annotation_revision_id"] == str(fixture.selected_revision_id)
    assert snapshot["cases"][0]["artifacts"][0]["sha256"] == "f" * 64
    assert snapshot["changes"][0]["change_type"] == "added"
    assert export_response.status_code == 201
    assert repeated_export.json() == export_response.json()
    assert export_store.publication_count == 2
    assert download_response.status_code == 200
    assert "manifest.json" in download_response.json()["url"]
    assert history_response.status_code == 200
    assert history_response.json()["datasets"][0]["status"] == "frozen"
    assert detail_response.status_code == 200
    assert detail_response.json()["members"][0]["state"] == "gold"
    assert snapshot_response.json() == snapshot
    assert frozen_delete.status_code == 422


def _promotion(
    *,
    dataset_revision: int,
    state_version: int,
    target: str,
    actor_id,
    selected_revision_id=None,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "expected_dataset_revision": dataset_revision,
        "expected_state_version": state_version,
        "target_state": target,
        "selected_revision_id": (
            str(selected_revision_id) if selected_revision_id is not None else None
        ),
        "actor_id": str(actor_id),
        "rationale": f"Promote to {target}.",
    }
