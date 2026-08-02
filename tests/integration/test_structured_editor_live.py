"""Opt-in PostgreSQL + MinIO acceptance test for typed annotation revisions."""

import os
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import ArtifactVisibility, CaseArtifactRole, CaseSourceArtifact
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    PostgresEvaluationRepository,
    StructuredAnnotationService,
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
from tests.fakes.structured import structured_annotation

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_STRUCTURED_EDITOR") != "1",
    reason="set HIVEBLOT_RUN_LIVE_STRUCTURED_EDITOR=1 with local PostgreSQL and MinIO",
)


def test_live_structured_snapshot_round_trip_concurrency_and_undo() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    artifact = _upload_artifact(_artifact_service(settings))
    evaluation = EvaluationService(PostgresEvaluationRepository(settings.database_url))
    service = StructuredAnnotationService(evaluation)
    case = evaluation.create_case(
        case_key=f"structured-live:{uuid4()}",
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
    original = structured_annotation("TP53")
    document, first = service.save(
        case.case_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=None,
        annotation=original,
        rationale="live initial review",
        error_codes=(),
    )
    corrected = original.model_copy(
        update={
            "proteins": (
                original.proteins[0].model_copy(update={"name": "p53"}),
                *original.proteins[1:],
            )
        }
    )
    document, second = service.save(
        case.case_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=first.revision_id,
        annotation=corrected,
        rationale="live correction",
        error_codes=("incorrect_value",),
    )
    with pytest.raises(ConcurrencyConflict):
        service.save(
            case.case_id,
            reviewer_id=reviewer_id,
            expected_head_revision_id=first.revision_id,
            annotation=original,
            rationale="stale live save",
            error_codes=(),
        )
    _, restored = service.undo(
        document.annotation_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=second.revision_id,
        target_revision_id=first.revision_id,
        rationale="live restore",
    )

    revisions = evaluation.list_revisions(document.annotation_id)
    assert len(revisions) == 3
    assert revisions[0].structured_annotation == original
    assert revisions[1].structured_annotation == corrected
    assert restored.structured_annotation == original
    assert restored.prior_revision_id == second.revision_id


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
    content = b"\x89PNG\r\n\x1a\nPRD-006-structured-live"
    instructions = service.begin_multipart_upload(
        original_filename="prd-006-live.png",
        declared_media_type="image/png",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:test:prd-006-live",
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
