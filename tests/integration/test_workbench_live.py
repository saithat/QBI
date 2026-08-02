"""Opt-in PostgreSQL + MinIO acceptance test for the source workbench."""

import hashlib
import io
import os
from uuid import uuid4

import httpx
import psycopg
import pytest
from hiveblot_contracts import (
    ArtifactVisibility,
    BoundingRegion,
    CaseArtifactRole,
    CaseSourceArtifact,
    ModelIdentifier,
    PredictionEvidence,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    EvidenceWorkbenchService,
    PostgresEvaluationRepository,
    PostgresSourceContextRepository,
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

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_WORKBENCH") != "1",
    reason="set HIVEBLOT_RUN_LIVE_WORKBENCH=1 with local PostgreSQL and MinIO",
)


def test_live_workbench_context_overlay_and_direct_signed_source() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    artifact_service = _artifact_service(settings)
    content = _png_fixture()
    artifact = _upload_artifact(artifact_service, content)
    evaluation = EvaluationService(PostgresEvaluationRepository(settings.database_url))
    case = evaluation.create_case(
        case_key=f"workbench-live:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact.artifact_id,
                role=CaseArtifactRole.FIGURE,
                page_number=1,
            ),
        ),
    )
    region = BoundingRegion(
        region_id=uuid4(),
        source_artifact_id=artifact.artifact_id,
        x=10.0,
        y=8.0,
        width=35.0,
        height=16.0,
        canvas_width=100,
        canvas_height=50,
        page_number=1,
    )
    prediction = evaluation.add_prediction(
        case.case_id,
        prediction_schema="western-blot-extraction",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(provider="local", name="fixture", version="workbench"),
        pipeline=None,
        raw_output_json='{"target":"p53"}',
        normalized_output_json='{"target":"p53"}',
        configuration_json="{}",
        evidence=(
            PredictionEvidence(
                artifact_id=artifact.artifact_id,
                field_path="/targets/0/name",
                region_id=region.region_id,
                region=region,
                description="p53 blot",
            ),
        ),
        validation_issues=(),
        confidence=0.9,
        trace_id=uuid4(),
        latency_ms=1,
        cost_microusd=0,
    )
    workbench_service = EvidenceWorkbenchService(
        evaluation,
        artifact_service,
        PostgresSourceContextRepository(settings.database_url),
    )

    try:
        first_context = workbench_service.put_source_context(
            case.case_id,
            artifact.artifact_id,
            CaseArtifactRole.FIGURE,
            expected_head_revision_id=None,
            caption="Live workbench caption",
            nearby_text="Live nearby source text",
        )
        second_context = workbench_service.put_source_context(
            case.case_id,
            artifact.artifact_id,
            CaseArtifactRole.FIGURE,
            expected_head_revision_id=first_context.context_revision_id,
            caption="Corrected live workbench caption",
            nearby_text="Live nearby source text",
        )
        with pytest.raises(ConcurrencyConflict):
            workbench_service.put_source_context(
                case.case_id,
                artifact.artifact_id,
                CaseArtifactRole.FIGURE,
                expected_head_revision_id=first_context.context_revision_id,
                caption="Stale live caption",
                nearby_text=None,
            )
        workbench = workbench_service.get_workbench(case.case_id)
        context_history = workbench_service.list_source_context_revisions(
            case.case_id,
            artifact.artifact_id,
            CaseArtifactRole.FIGURE,
        )
        signed_url, _ = artifact_service.create_download_url(artifact.artifact_id, actor_id=None)
        downloaded = httpx.get(signed_url)

        assert workbench.sources[0].caption == "Corrected live workbench caption"
        assert context_history == (first_context, second_context)
        assert workbench.sources[0].artifact.sha256 == hashlib.sha256(content).hexdigest()
        assert workbench.overlays[0].prediction_id == prediction.prediction_id
        assert workbench.overlays[0].region == region
        assert downloaded.content == content
        assert downloaded.headers["content-type"].startswith("image/png")
    finally:
        with psycopg.connect(settings.database_url) as connection:
            connection.execute(
                "DELETE FROM evaluation_case_source_contexts WHERE case_id = %s",
                (case.case_id,),
            )
            connection.execute(
                "DELETE FROM prediction_documents WHERE case_id = %s", (case.case_id,)
            )
            connection.execute(
                "DELETE FROM evaluation_case_artifacts WHERE case_id = %s",
                (case.case_id,),
            )
            connection.execute("DELETE FROM evaluation_cases WHERE case_id = %s", (case.case_id,))


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
        original_filename="prd-005-live.png",
        declared_media_type="image/png",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:test:prd-005-live",
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
    Image.new("L", (100, 50), color=180).save(stream, format="PNG", optimize=False)
    return stream.getvalue()
