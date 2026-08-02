"""Opt-in PostgreSQL + MinIO acceptance test for source-pixel geometry revisions."""

import io
import os
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import (
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceArtifact,
    ModelIdentifier,
    PredictionEvidence,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    PostgresEvaluationRepository,
    SpatialAnnotationService,
)
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    PostgresArtifactRepository,
    S3ObjectStore,
    SourceAdapterRegistry,
)
from PIL import Image

from hiveblot import db
from hiveblot.settings import Settings
from tests.fakes.spatial import spatial_annotation_set

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_SPATIAL_EDITOR") != "1",
    reason="set HIVEBLOT_RUN_LIVE_SPATIAL_EDITOR=1 with local PostgreSQL and MinIO",
)


def test_live_spatial_graph_round_trip_concurrency_and_undo() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    artifact = _upload_artifact(_artifact_service(settings), _png_fixture())
    evaluation = EvaluationService(PostgresEvaluationRepository(settings.database_url))
    service = SpatialAnnotationService(evaluation)
    case = evaluation.create_case(
        case_key=f"spatial-live:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact.artifact_id,
                role=CaseArtifactRole.FIGURE,
                page_number=1,
            ),
        ),
    )
    reviewer_id = uuid4()
    original = spatial_annotation_set(artifact.artifact_id)
    predicted_regions = original.spatial_annotations[3:5]
    prediction = evaluation.add_prediction(
        case.case_id,
        prediction_schema="western-blot-spatial",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(provider="fixture", name="detector", version="prd-007"),
        pipeline=None,
        raw_output_json="live source-pixel regions",
        normalized_output_json="{}",
        configuration_json="{}",
        evidence=tuple(
            PredictionEvidence(
                artifact_id=artifact.artifact_id,
                field_path=f"/lanes/{index}",
                region_id=annotation.region.region_id,
                region=annotation.region,
                description=annotation.label or "lane",
            )
            for index, annotation in enumerate(predicted_regions)
        ),
        validation_issues=(),
        confidence=0.91,
        trace_id=uuid4(),
        latency_ms=2,
        cost_microusd=0,
    )
    document, first = service.save(
        case.case_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=None,
        annotation_set=original,
        rationale="live initial geometry",
        error_codes=(),
    )
    band = original.spatial_annotations[7]
    corrected = original.model_copy(
        update={
            "spatial_annotations": (
                *original.spatial_annotations[:7],
                band.model_copy(
                    update={"region": band.region.model_copy(update={"x": band.region.x + 2})}
                ),
                *original.spatial_annotations[8:],
            )
        }
    )
    _, second = service.save(
        case.case_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=first.revision_id,
        annotation_set=corrected,
        rationale="live geometry correction",
        error_codes=("incorrect_geometry",),
    )
    with pytest.raises(ConcurrencyConflict):
        service.save(
            case.case_id,
            reviewer_id=reviewer_id,
            expected_head_revision_id=first.revision_id,
            annotation_set=original,
            rationale="stale live geometry",
            error_codes=(),
        )
    _, restored = service.undo(
        document.annotation_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=second.revision_id,
        target_revision_id=first.revision_id,
        rationale="live geometry restore",
    )

    revisions = evaluation.list_revisions(document.annotation_id)
    assert len(revisions) == 3
    _, prediction_snapshot = service.prediction_set(case.case_id, prediction.prediction_id)
    assert prediction_snapshot.spatial_annotations == predicted_regions
    _assert_same_graph(service.annotation_set(revisions[0]), original)
    _assert_same_graph(service.annotation_set(revisions[1]), corrected)
    _assert_same_graph(service.annotation_set(restored), original)
    assert restored.prior_revision_id == second.revision_id


def _assert_same_graph(actual, expected) -> None:
    assert frozenset(actual.spatial_annotations) == frozenset(expected.spatial_annotations)
    assert frozenset(actual.relationships) == frozenset(expected.relationships)


def _artifact_service(settings: Settings) -> ArtifactService:
    store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    store.ensure_bucket()
    return ArtifactService(
        repository=PostgresArtifactRepository(settings.database_url),
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=settings.artifact_max_bytes,
        upload_url_seconds=settings.artifact_upload_url_seconds,
        download_url_seconds=settings.artifact_signed_url_seconds,
    )


def _upload_artifact(service: ArtifactService, content: bytes):
    instructions = service.begin_multipart_upload(
        original_filename="prd-007-live.png",
        declared_media_type="image/png",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:test:prd-007-live",
        relationships=(),
        actor_id=None,
    )
    upload = httpx.put(instructions.parts[0].url, content=content)
    upload.raise_for_status()
    return service.complete_multipart_upload(
        instructions.upload_id,
        (CompletedPart(part_number=1, etag=upload.headers["etag"]),),
        actor_id=None,
    ).artifact


def _png_fixture() -> bytes:
    stream = io.BytesIO()
    Image.new("L", (400, 200), color=180).save(stream, format="PNG", optimize=False)
    return stream.getvalue()
