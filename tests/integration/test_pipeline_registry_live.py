"""Opt-in PostgreSQL + MinIO acceptance test for the immutable pipeline registry."""

import io
import os
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import (
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceArtifact,
    PublishedPipelineValue,
    SpatialAnnotationSet,
)
from hiveblot_evaluation import (
    EvaluationService,
    PipelineRegistryService,
    PostgresEvaluationRepository,
    PostgresPipelineRunRepository,
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
from tests.fakes.pipeline import pipeline_definition
from tests.fakes.structured import structured_annotation

pytestmark = pytest.mark.skipif(
    os.getenv("HIVEBLOT_RUN_LIVE_PIPELINE") != "1",
    reason="set HIVEBLOT_RUN_LIVE_PIPELINE=1 with local PostgreSQL and MinIO",
)


def test_live_pipeline_replay_publication_and_repository_reload() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    artifacts = _artifact_service(settings)
    source = _upload_artifact(
        artifacts,
        filename="prd-008-source.png",
        media_type="image/png",
        content=_png_fixture(),
    )
    output = _upload_artifact(
        artifacts,
        filename="prd-008-output.json",
        media_type="application/json",
        content=b'{"artifact":"normalized-output"}',
    )
    evaluation = EvaluationService(PostgresEvaluationRepository(settings.database_url))
    case = evaluation.create_case(
        case_key=f"pipeline-live:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=source.artifact_id,
                role=CaseArtifactRole.FIGURE,
            ),
        ),
    )
    repository = PostgresPipelineRunRepository(settings.database_url)
    service = PipelineRegistryService(repository, evaluation, artifacts)
    fixture = pipeline_definition()
    definition = service.register_definition(
        pipeline=fixture.pipeline.model_copy(update={"version": f"live-{uuid4()}"}),
        description=fixture.description,
        components=fixture.components,
        configuration_json=fixture.configuration_json,
    )
    detail = service.create_run(
        case.case_id,
        definition_id=definition.definition_id,
        input_artifact_ids=(source.artifact_id,),
        configuration_json='{"mode":"live"}',
        trace_id=uuid4(),
    )
    first, second = detail.invocations
    service.complete_success(
        first.invocation_id,
        output_schema=first.component.output_schema,
        raw_output_json="raw spatial output",
        normalized_output_json=SpatialAnnotationSet().model_dump_json(),
        output_artifact_ids=(),
        evidence=(),
        validation_issues=(),
        latency_ms=3,
        cost_microusd=0,
    )
    failed = service.complete_success(
        second.invocation_id,
        output_schema=second.component.output_schema,
        raw_output_json="raw invalid metadata",
        normalized_output_json='{"schema_version":"1.0","unknown":true}',
        output_artifact_ids=(),
        evidence=(),
        validation_issues=(),
        latency_ms=5,
        cost_microusd=2,
    )
    replay = service.replay(
        second.invocation_id,
        configuration_json='{"prompt":"corrected"}',
        trace_id=uuid4(),
    )
    normalized = structured_annotation("TP53").model_dump_json()
    completed = service.complete_success(
        replay.invocation_id,
        output_schema=replay.component.output_schema,
        raw_output_json="raw corrected metadata",
        normalized_output_json=normalized,
        output_artifact_ids=(output.artifact_id,),
        evidence=(),
        validation_issues=(),
        latency_ms=4,
        cost_microusd=1,
    )
    publication = service.publish(
        detail.run.run_id,
        output_schema=completed.component.output_schema,
        normalized_output_json=normalized,
        values=(
            PublishedPipelineValue(
                field_path="",
                producing_invocation_id=completed.invocation_id,
                result_path="",
                value_json=normalized,
            ),
        ),
        output_artifact_ids=(output.artifact_id,),
        trace_id=uuid4(),
    )

    reloaded = PipelineRegistryService(
        PostgresPipelineRunRepository(settings.database_url),
        EvaluationService(PostgresEvaluationRepository(settings.database_url)),
        _artifact_service(settings),
    ).get_run_detail(detail.run.run_id)

    assert failed.result is not None
    assert failed.result.raw_output_json == "raw invalid metadata"
    assert reloaded.run.status.value == "published"
    assert len(reloaded.invocations) == 3
    reloaded_by_id = {item.invocation_id: item for item in reloaded.invocations}
    assert reloaded_by_id[failed.invocation_id] == failed
    assert reloaded_by_id[replay.invocation_id].replay_of_invocation_id == failed.invocation_id
    assert reloaded.publications == (publication,)


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


def _upload_artifact(
    service: ArtifactService,
    *,
    filename: str,
    media_type: str,
    content: bytes,
):
    instructions = service.begin_multipart_upload(
        original_filename=filename,
        declared_media_type=media_type,
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri=f"urn:hiveblot:test:{filename}:{uuid4()}",
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
    Image.new("L", (80, 40), color=160).save(stream, format="PNG", optimize=False)
    return stream.getvalue()
