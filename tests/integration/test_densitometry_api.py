from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from hiveblot_contracts import (
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceArtifact,
)
from hiveblot_densitometry import DensitometryGeometryResolver, DensitometryService
from hiveblot_evaluation import EvaluationService, PipelineRegistryService, SpatialAnnotationService
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    SourceAdapterRegistry,
    VerifiedArtifactReader,
)

from apps.api.application import app
from apps.api.artifact_dependencies import get_artifact_service
from apps.api.densitometry_dependencies import get_densitometry_service
from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore
from tests.fakes.densitometry import (
    TARGET_IDS,
    synthetic_blot_png,
    synthetic_densitometry_annotation_set,
)
from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.pipeline_registry import InMemoryPipelineRunRepository

NOW = datetime(2026, 8, 2, 21, tzinfo=UTC)
REVIEWER_ID = UUID("70000000-0000-0000-0000-000000000002")


def test_densitometry_api_runs_lists_gets_and_replays() -> None:
    service, artifact_service, case_id, artifact_id, revision_id = _api_fixture()
    app.dependency_overrides[get_densitometry_service] = lambda: service
    app.dependency_overrides[get_artifact_service] = lambda: artifact_service
    client = TestClient(app)
    try:
        page = client.get(f"/densitometry/{case_id}")
        assert page.status_code == 200
        assert "Run deterministic analysis" in page.text
        assert client.get("/review/assets/densitometry.css").status_code == 200
        assert client.get("/review/assets/densitometry.js").status_code == 200

        workbench = client.get(f"/api/v1/evaluation-cases/{case_id}/densitometry-workbench")
        assert workbench.status_code == 200, workbench.text
        option = workbench.json()["geometry_options"][0]
        assert option["geometry"] == {
            "schema_version": "1.0",
            "source_type": "reviewer_revision",
            "annotation_revision_id": str(revision_id),
        }
        assert option["image_download_url"].startswith("memory://download/")
        assert option["inferred_loading_control_target_ids"] == [str(TARGET_IDS[1])]

        invalid = client.post(
            f"/api/v1/evaluation-cases/{case_id}/densitometry-runs",
            json={
                "schema_version": "1.0",
                "image_artifact_id": str(artifact_id),
                "geometry": {
                    "schema_version": "1.0",
                    "source_type": "reviewer_revision",
                    "annotation_revision_id": str(revision_id),
                },
                "future_field": True,
            },
        )
        assert invalid.status_code == 422

        started = client.post(
            f"/api/v1/evaluation-cases/{case_id}/densitometry-runs",
            json={
                "schema_version": "1.0",
                "image_artifact_id": str(artifact_id),
                "geometry": {
                    "schema_version": "1.0",
                    "source_type": "reviewer_revision",
                    "annotation_revision_id": str(revision_id),
                },
                "loading_control_target_id": str(TARGET_IDS[1]),
                "configuration": {
                    "schema_version": "1.0",
                    "background_method": "global_percentile",
                    "normalization_method": "loading_control",
                    "background_percentile": 20.0,
                    "local_border_pixels": 4,
                    "saturation_black_level": 4,
                    "saturation_fraction_threshold": 0.05,
                    "minimum_band_width_pixels": 3,
                    "minimum_band_height_pixels": 2,
                    "minimum_band_area_pixels": 9,
                    "uneven_background_cv_threshold": 10.0,
                    "lane_boundaries_reviewed": True,
                    "exposure_known": True,
                },
            },
        )
        assert started.status_code == 201, started.text
        payload = started.json()
        assert payload["result"]["suitability"] == "quantitative"
        assert payload["result"]["measurements"][0]["corrected_intensity"] == 5600.0
        assert payload["source_image_download_url"].startswith("memory://download/")
        assert payload["overlay_download_url"].startswith("memory://download/")

        fetched = client.get(f"/api/v1/densitometry-invocations/{payload['invocation_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["result"]["result_id"] == payload["result"]["result_id"]

        replayed = client.post(
            f"/api/v1/densitometry-invocations/{payload['invocation_id']}/replays",
            json={"schema_version": "1.0"},
        )
        assert replayed.status_code == 201, replayed.text
        replay_payload = replayed.json()
        assert replay_payload["replay_of_invocation_id"] == payload["invocation_id"]
        assert replay_payload["invocation_id"] != payload["invocation_id"]
        assert replay_payload["result"]["result_id"] == payload["result"]["result_id"]

        refreshed = client.get(f"/api/v1/evaluation-cases/{case_id}/densitometry-workbench")
        assert len(refreshed.json()["attempts"]) == 2
    finally:
        app.dependency_overrides.clear()


def _api_fixture() -> tuple[DensitometryService, ArtifactService, UUID, UUID, UUID]:
    artifact_repository = InMemoryArtifactRepository()
    object_store = InMemoryObjectStore()
    artifact_service = ArtifactService(
        repository=artifact_repository,
        object_store=object_store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=10_000_000,
        upload_url_seconds=3600,
        download_url_seconds=900,
        clock=lambda: NOW,
    )
    content = synthetic_blot_png()
    upload = artifact_service.begin_multipart_upload(
        original_filename="synthetic-blot.png",
        declared_media_type="image/png",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:fixture:densitometry-api",
        relationships=(),
        actor_id=None,
    )
    session = artifact_repository.uploads[upload.upload_id]
    assert session.backend_upload_id is not None
    etag = object_store.put_part(session.backend_upload_id, 1, content)
    artifact = artifact_service.complete_multipart_upload(
        upload.upload_id,
        (CompletedPart(part_number=1, etag=etag),),
        actor_id=None,
    ).artifact
    reader = VerifiedArtifactReader(artifact_service, artifact_repository, object_store)
    evaluation = EvaluationService(InMemoryEvaluationRepository(), clock=lambda: NOW)
    case = evaluation.create_case(
        case_key=f"densitometry-api:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact.artifact_id,
                role=CaseArtifactRole.RAW_SOURCE,
            ),
        ),
    )
    spatial = SpatialAnnotationService(evaluation)
    _, revision = spatial.save(
        case.case_id,
        reviewer_id=REVIEWER_ID,
        expected_head_revision_id=None,
        annotation_set=synthetic_densitometry_annotation_set(artifact.artifact_id),
        rationale="API fixture geometry",
        error_codes=(),
    )
    pipelines = PipelineRegistryService(
        InMemoryPipelineRunRepository(),
        evaluation,
        artifact_service,
        clock=lambda: NOW,
    )
    geometry = DensitometryGeometryResolver(evaluation, spatial, reader)
    service = DensitometryService(reader, artifact_service, pipelines, geometry)
    return service, artifact_service, case.case_id, artifact.artifact_id, revision.revision_id
