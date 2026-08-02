"""Opt-in PostgreSQL + MinIO acceptance test for PRD-011 extraction migration."""

import io
import json
import os
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import (
    ArtifactVisibility,
    ReviewStatus,
    WesternBlotExtractionConfiguration,
    WesternBlotExtractionResult,
)
from hiveblot_evaluation import (
    EvaluationService,
    PipelineRegistryService,
    PostgresEvaluationRepository,
    PostgresPipelineRunRepository,
)
from hiveblot_extraction import (
    ExtractionImplementationRegistry,
    VerifiedExtractionArtifactReader,
    WesternBlotExtractionService,
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
from tests.fakes.extraction import FixtureExtractionImplementation

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_EXTRACTION") != "1",
    reason="set HIVEBLOT_RUN_LIVE_EXTRACTION=1 with local PostgreSQL and MinIO",
)


def test_live_stored_image_pipeline_rerun_and_replay_preserve_review() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    store = _object_store(settings)
    repository = PostgresArtifactRepository(settings.database_url)
    artifact_service = ArtifactService(
        repository=repository,
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=settings.artifact_max_bytes,
        upload_url_seconds=settings.artifact_upload_url_seconds,
        download_url_seconds=settings.artifact_signed_url_seconds,
    )
    artifact = _upload_unique_image(artifact_service)
    reader = VerifiedExtractionArtifactReader(artifact_service, repository, store)
    evaluation = EvaluationService(PostgresEvaluationRepository(settings.database_url))
    pipelines = PipelineRegistryService(
        PostgresPipelineRunRepository(settings.database_url),
        evaluation,
        artifact_service,
    )
    implementation = FixtureExtractionImplementation()
    service = WesternBlotExtractionService(
        reader,
        evaluation,
        pipelines,
        ExtractionImplementationRegistry(
            {implementation.identity.implementation_name: implementation}
        ),
    )
    configuration = WesternBlotExtractionConfiguration(
        implementation_name=implementation.identity.implementation_name
    )

    first = service.run(artifact.artifact_id, configuration=configuration)
    document, revision = evaluation.create_annotation(
        first.case_id,
        reviewer_id=uuid4(),
        rationale="Live review retained across extraction reruns.",
        error_codes=(),
        field_annotations=(),
        spatial_annotations=(),
        relationships=(),
        structured_annotation=first.result.structured_annotation,
    )
    current = evaluation.get_case(first.case_id)
    evaluation.update_case_status(
        first.case_id,
        expected_version=current.version,
        review_status=ReviewStatus.REVIEWED,
    )
    second = service.run(artifact.artifact_id, configuration=configuration)
    replay = service.replay_component(first.assembler_invocation_id)

    reloaded_evaluation = EvaluationService(PostgresEvaluationRepository(settings.database_url))
    reloaded_pipelines = PipelineRegistryService(
        PostgresPipelineRunRepository(settings.database_url),
        reloaded_evaluation,
        artifact_service,
    )
    predictions = reloaded_evaluation.list_predictions(first.case_id)
    runs = reloaded_pipelines.list_runs(first.case_id)
    stored_result = WesternBlotExtractionResult.model_validate_json(
        reloaded_evaluation.get_prediction(first.prediction_id).normalized_output_json or ""
    )

    assert second.case_id == first.case_id
    assert second.run_id != first.run_id
    assert replay.prediction_id is not None
    assert len(predictions) == 3
    assert len(runs) == 2
    assert len(runs[0].publications) + len(runs[1].publications) == 3
    assert stored_result == first.result
    assert reloaded_evaluation.get_annotation(document.annotation_id).head_revision_id == (
        revision.revision_id
    )
    assert len(reloaded_evaluation.list_revisions(document.annotation_id)) == 1
    assert json.loads(predictions[0].raw_output_json)["responses"][0]["raw_output"] == (
        implementation.raw_output
    )


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


def _upload_unique_image(service: ArtifactService):
    unique = uuid4().bytes
    color = (unique[0], unique[1], unique[2])
    buffer = io.BytesIO()
    Image.new("RGB", (2696, 3500), color).save(buffer, format="PNG")
    content = buffer.getvalue()
    instructions = service.begin_multipart_upload(
        original_filename="prd-011-live.png",
        declared_media_type="image/png",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri=f"urn:hiveblot:test:prd-011:{uuid4()}",
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
