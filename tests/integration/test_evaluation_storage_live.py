"""Opt-in PostgreSQL acceptance test for PRD-003 immutable review persistence."""

import os
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import (
    AnnotationRelationship,
    ArtifactVisibility,
    BoundingRegion,
    CaseArtifactRole,
    CaseSourceArtifact,
    FieldAnnotation,
    FieldPathTarget,
    ModelIdentifier,
    ObservationState,
    ReviewStatus,
    SpatialAnnotation,
    SpatialAnnotationType,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    PostgresEvaluationRepository,
)
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    PostgresArtifactRepository,
    S3ObjectStore,
    SourceAdapterRegistry,
)

from hiveblot import db
from hiveblot.settings import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_EVALUATION") != "1",
    reason="set HIVEBLOT_RUN_LIVE_EVALUATION=1 with local PostgreSQL and MinIO",
)


def test_live_predictions_independent_reviews_revisions_and_adjudication() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    artifact_service = _artifact_service(settings)
    artifact = _upload_artifact(artifact_service)
    service = EvaluationService(PostgresEvaluationRepository(settings.database_url))
    case = service.create_case(
        case_key=f"live:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact.artifact_id,
                role=CaseArtifactRole.FIGURE,
                page_number=1,
            ),
        ),
    )
    for version in ("baseline", "candidate"):
        service.add_prediction(
            case.case_id,
            prediction_schema="western-blot-extraction",
            prediction_schema_version="1.0",
            producer=ModelIdentifier(provider="local", name="fixture", version=version),
            pipeline=None,
            raw_output_json=f"raw-{version}",
            normalized_output_json='{"target":"p53"}',
            configuration_json="{}",
            evidence=(),
            validation_issues=(),
            confidence=0.8,
            trace_id=uuid4(),
            latency_ms=1,
            cost_microusd=0,
        )

    snapshot = _snapshot(artifact.artifact_id, "p53")
    reviewer_one = uuid4()
    reviewer_two = uuid4()
    first_document, first_revision = service.create_annotation(
        case.case_id,
        reviewer_id=reviewer_one,
        rationale="first review",
        error_codes=("incorrect_value",),
        field_annotations=snapshot[0],
        spatial_annotations=snapshot[1],
        relationships=snapshot[2],
    )
    _, independent_revision = service.create_annotation(
        case.case_id,
        reviewer_id=reviewer_two,
        rationale="independent review",
        error_codes=(),
        field_annotations=snapshot[0],
        spatial_annotations=snapshot[1],
        relationships=snapshot[2],
    )
    corrected = _snapshot(artifact.artifact_id, "TP53")
    _, corrected_revision = service.append_revision(
        first_document.annotation_id,
        expected_head_revision_id=first_revision.revision_id,
        reviewer_id=reviewer_one,
        rationale="canonicalized target",
        error_codes=(),
        field_annotations=corrected[0],
        spatial_annotations=corrected[1],
        relationships=corrected[2],
    )
    with pytest.raises(ConcurrencyConflict):
        service.append_revision(
            first_document.annotation_id,
            expected_head_revision_id=first_revision.revision_id,
            reviewer_id=reviewer_one,
            rationale="stale edit",
            error_codes=(),
            field_annotations=snapshot[0],
            spatial_annotations=snapshot[1],
            relationships=snapshot[2],
        )

    current = service.get_case(case.case_id)
    service.update_case_status(
        case.case_id,
        expected_version=current.version,
        review_status=ReviewStatus.NEEDS_ADJUDICATION,
    )
    adjudication = service.adjudicate(
        case.case_id,
        adjudicator_id=uuid4(),
        selected_revision_id=corrected_revision.revision_id,
        considered_revision_ids=(
            corrected_revision.revision_id,
            independent_revision.revision_id,
        ),
        rationale="canonical target spelling wins",
    )

    assert len(service.list_predictions(case.case_id)) == 2
    assert service.list_revisions(first_document.annotation_id) == (
        first_revision,
        corrected_revision,
    )
    assert adjudication.selected_revision_id == corrected_revision.revision_id
    assert service.get_case(case.case_id).review_status is ReviewStatus.ADJUDICATED


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


def _upload_artifact(service: ArtifactService):
    content = b"\x89PNG\r\n\x1a\nPRD-003-live-figure"
    instructions = service.begin_multipart_upload(
        original_filename="prd-003-live.png",
        declared_media_type="image/png",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:test:prd-003-live",
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


def _snapshot(artifact_id, target):
    spatial_id = uuid4()
    field_id = uuid4()
    return (
        (
            FieldAnnotation(
                field_annotation_id=field_id,
                target=FieldPathTarget(field_path="/targets/0/name"),
                state=ObservationState.PRESENT,
                value=target,
                original_extracted_text="P53",
                evidence_region_ids=(spatial_id,),
            ),
        ),
        (
            SpatialAnnotation(
                spatial_annotation_id=spatial_id,
                annotation_type=SpatialAnnotationType.BAND,
                state=ObservationState.PRESENT,
                region=BoundingRegion(
                    region_id=spatial_id,
                    source_artifact_id=artifact_id,
                    x=1.0,
                    y=2.0,
                    width=3.0,
                    height=4.0,
                    canvas_width=20,
                    canvas_height=20,
                    page_number=1,
                ),
                label=target,
            ),
        ),
        (
            AnnotationRelationship(
                relationship_id=uuid4(),
                subject_id=field_id,
                relation_type="supported_by",
                object_id=spatial_id,
            ),
        ),
    )
