"""Opt-in PostgreSQL + MinIO acceptance test for deterministic densitometry."""

from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRelationshipKind,
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceArtifact,
    ReviewerGeometryReference,
)
from hiveblot_densitometry import DensitometryGeometryResolver, DensitometryService
from hiveblot_evaluation import (
    EvaluationService,
    PipelineRegistryService,
    PostgresEvaluationRepository,
    PostgresPipelineRunRepository,
    SpatialAnnotationService,
)
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    PostgresArtifactRepository,
    S3ObjectStore,
    SourceAdapterRegistry,
    VerifiedArtifactReader,
)

from hiveblot import db
from hiveblot.settings import Settings
from tests.fakes.densitometry import (
    TARGET_IDS,
    synthetic_blot_png,
    synthetic_densitometry_annotation_set,
    synthetic_densitometry_input,
)

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_DENSITOMETRY") != "1",
    reason="set HIVEBLOT_RUN_LIVE_DENSITOMETRY=1 with local PostgreSQL and MinIO",
)


def test_live_densitometry_is_exact_persisted_and_replayable() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    store = _object_store(settings)
    artifact_repository = PostgresArtifactRepository(settings.database_url)
    artifact_service = ArtifactService(
        repository=artifact_repository,
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=settings.artifact_max_bytes,
        upload_url_seconds=settings.artifact_upload_url_seconds,
        download_url_seconds=settings.artifact_signed_url_seconds,
    )
    source = _upload_image(artifact_service)
    reader = VerifiedArtifactReader(artifact_service, artifact_repository, store)
    evaluation = EvaluationService(PostgresEvaluationRepository(settings.database_url))
    spatial = SpatialAnnotationService(evaluation)
    case = evaluation.create_case(
        case_key=f"densitometry-live:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=source.artifact_id,
                role=CaseArtifactRole.RAW_SOURCE,
            ),
        ),
    )
    _, geometry_revision = spatial.save(
        case.case_id,
        reviewer_id=uuid4(),
        expected_head_revision_id=None,
        annotation_set=synthetic_densitometry_annotation_set(source.artifact_id),
        rationale="Live exact geometry",
        error_codes=(),
    )
    pipelines = PipelineRegistryService(
        PostgresPipelineRunRepository(settings.database_url),
        evaluation,
        artifact_service,
    )
    service = DensitometryService(
        reader,
        artifact_service,
        pipelines,
        DensitometryGeometryResolver(evaluation, spatial, reader),
    )
    first = service.run(
        case.case_id,
        image_artifact_id=source.artifact_id,
        geometry=ReviewerGeometryReference(annotation_revision_id=geometry_revision.revision_id),
        loading_control_target_id=TARGET_IDS[1],
        configuration=synthetic_densitometry_input().configuration,
    )
    replay = service.replay(first.invocation_id)

    reloaded_evaluation = EvaluationService(PostgresEvaluationRepository(settings.database_url))
    reloaded_reader = VerifiedArtifactReader(
        artifact_service,
        PostgresArtifactRepository(settings.database_url),
        store,
    )
    reloaded = DensitometryService(
        reloaded_reader,
        artifact_service,
        PipelineRegistryService(
            PostgresPipelineRunRepository(settings.database_url),
            reloaded_evaluation,
            artifact_service,
        ),
        DensitometryGeometryResolver(
            reloaded_evaluation,
            SpatialAnnotationService(reloaded_evaluation),
            reloaded_reader,
        ),
    )
    attempts = reloaded.list_attempts(case.case_id)
    stored = reloaded.get_attempt(first.invocation_id)
    overlay = artifact_service.get_artifact(stored.result.analysis_overlay.artifact_id)

    assert len(attempts) == 2
    assert replay.result == first.result
    assert replay.invocation_id != first.invocation_id
    assert stored.result.measurements[0].corrected_intensity == 5600.0
    assert stored.result.measurements[0].normalized_intensity == 1.166667
    assert overlay.acquisition_method is ArtifactAcquisitionMethod.TOOL_OUTPUT
    assert len(overlay.relationships) == 1
    assert overlay.relationships[0].related_artifact_id == source.artifact_id
    assert overlay.relationships[0].kind is ArtifactRelationshipKind.DERIVED_FROM
    assert reloaded_reader.read_bytes(overlay.artifact_id).startswith(b"\x89PNG\r\n\x1a\n")


def _object_store(settings: Settings) -> S3ObjectStore:
    store = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    store.ensure_bucket()
    return store


def _upload_image(service: ArtifactService):
    content = synthetic_blot_png()
    instructions = service.begin_multipart_upload(
        original_filename="prd-012-live.png",
        declared_media_type="image/png",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri=f"urn:hiveblot:test:prd-012:{uuid4()}",
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
