"""Opt-in PostgreSQL + MinIO acceptance test for golden snapshots and exports."""

import hashlib
import os
from uuid import uuid4

import httpx
import pytest
from hiveblot_contracts import (
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceArtifact,
    GoldenCaseState,
    GoldenDatasetCaseExport,
    GoldenDatasetExportManifest,
    GoldenDatasetSplit,
    GoldenDatasetType,
    ModelIdentifier,
    ReviewStatus,
)
from hiveblot_evaluation import (
    ContentAddressedDatasetExportStore,
    EvaluationService,
    GoldenDatasetExportPublisher,
    GoldenDatasetService,
    InvalidEvaluationState,
    PostgresEvaluationRepository,
    PostgresGoldenDatasetRepository,
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
    os.getenv("HIVEBLOT_RUN_LIVE_GOLDEN") != "1",
    reason="set HIVEBLOT_RUN_LIVE_GOLDEN=1 with local PostgreSQL and MinIO",
)


def test_live_golden_snapshot_export_and_repository_reload() -> None:
    settings = Settings(_env_file=None)
    db.initialize(settings.database_url)
    object_store = _object_store(settings)
    artifacts = _artifact_service(settings, object_store)
    artifact = _upload_artifact(artifacts)
    evaluation = EvaluationService(PostgresEvaluationRepository(settings.database_url))
    case = evaluation.create_case(
        case_key=f"golden-live:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact.artifact_id,
                role=CaseArtifactRole.FIGURE,
                page_number=1,
            ),
        ),
    )
    evaluation.add_prediction(
        case.case_id,
        prediction_schema="western-blot-structured-annotation",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(provider="local", name="fixture", version="1.0"),
        pipeline=None,
        raw_output_json='{"target":"TP53"}',
        normalized_output_json='{"target":"TP53"}',
        configuration_json="{}",
        evidence=(),
        validation_issues=(),
        confidence=0.9,
        trace_id=uuid4(),
        latency_ms=1,
        cost_microusd=0,
    )
    document, revision = evaluation.create_annotation(
        case.case_id,
        reviewer_id=uuid4(),
        rationale="Live golden review.",
        error_codes=(),
        field_annotations=(),
        spatial_annotations=(),
        relationships=(),
        structured_annotation=structured_annotation("TP53"),
    )
    current_case = evaluation.get_case(case.case_id)
    evaluation.update_case_status(
        case.case_id,
        expected_version=current_case.version,
        review_status=ReviewStatus.REVIEWED,
    )
    repository = PostgresGoldenDatasetRepository(settings.database_url)
    service = GoldenDatasetService(
        repository,
        evaluation,
        artifacts,
        GoldenDatasetExportPublisher(ContentAddressedDatasetExportStore(object_store)),
        signed_url_seconds=settings.artifact_signed_url_seconds,
    )
    actor_id = uuid4()
    dataset = service.create_dataset(
        dataset_name=f"golden-live-{uuid4()}",
        dataset_version="1.0",
        dataset_type=GoldenDatasetType.FROZEN_TEST,
        predecessor_snapshot_id=None,
        created_by=actor_id,
    )
    paper_key = f"doi:live-{uuid4()}"
    detail = service.add_case(
        dataset.dataset_id,
        case.case_id,
        expected_dataset_revision=1,
        paper_key=paper_key,
        split=GoldenDatasetSplit.TEST,
        actor_id=actor_id,
        rationale="Live reference case.",
    )
    related_case = evaluation.create_case(
        case_key=f"golden-live-related:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact.artifact_id,
                role=CaseArtifactRole.FIGURE,
                page_number=2,
            ),
        ),
    )
    with pytest.raises(InvalidEvaluationState, match="paper"):
        service.add_case(
            dataset.dataset_id,
            related_case.case_id,
            expected_dataset_revision=detail.dataset.revision,
            paper_key=paper_key,
            split=GoldenDatasetSplit.TRAIN,
            actor_id=actor_id,
            rationale="Must remain in the paper split.",
        )
    duplicate_content_case = evaluation.create_case(
        case_key=f"golden-live-duplicate:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact.artifact_id,
                role=CaseArtifactRole.FIGURE,
                page_number=3,
            ),
        ),
    )
    with pytest.raises(InvalidEvaluationState, match="artifact content"):
        service.add_case(
            dataset.dataset_id,
            duplicate_content_case.case_id,
            expected_dataset_revision=detail.dataset.revision,
            paper_key=f"doi:other-{uuid4()}",
            split=GoldenDatasetSplit.TRAIN,
            actor_id=actor_id,
            rationale="Must not leak identical content.",
        )
    for target in (
        GoldenCaseState.PREDICTED,
        GoldenCaseState.REVIEWED,
        GoldenCaseState.GOLD_CANDIDATE,
        GoldenCaseState.GOLD,
    ):
        member = detail.members[0]
        detail = service.promote_case(
            dataset.dataset_id,
            case.case_id,
            expected_dataset_revision=detail.dataset.revision,
            expected_state_version=member.state_version,
            target_state=target,
            selected_revision_id=(
                revision.revision_id if target is GoldenCaseState.REVIEWED else None
            ),
            actor_id=actor_id,
            rationale=f"Live promotion to {target.value}.",
        )
    snapshot = service.freeze(
        dataset.dataset_id,
        expected_dataset_revision=detail.dataset.revision,
        frozen_by=actor_id,
    )
    export = service.publish_export(snapshot.snapshot_id)

    reloaded_repository = PostgresGoldenDatasetRepository(settings.database_url)
    reloaded_snapshot = reloaded_repository.get_snapshot(snapshot.snapshot_id)
    reloaded_export = reloaded_repository.get_export(snapshot.snapshot_id)
    cases_bytes = b"".join(object_store.iter_bytes(export.cases_jsonl.storage_key))
    manifest_bytes = b"".join(object_store.iter_bytes(export.manifest_json.storage_key))
    signed_url, _ = service.create_export_download_url(
        snapshot.snapshot_id,
        export_kind="manifest_json",
    )
    downloaded_manifest = httpx.get(signed_url)
    downloaded_manifest.raise_for_status()

    assert reloaded_snapshot == snapshot
    assert reloaded_export == export
    assert service.publish_export(snapshot.snapshot_id) == export
    assert hashlib.sha256(cases_bytes).hexdigest() == export.cases_jsonl.sha256
    assert hashlib.sha256(manifest_bytes).hexdigest() == export.manifest_json.sha256
    assert GoldenDatasetCaseExport.model_validate_json(
        cases_bytes.splitlines()[0]
    ).case.case_id == (case.case_id)
    manifest = GoldenDatasetExportManifest.model_validate_json(manifest_bytes)
    assert manifest.dataset_sha256 == snapshot.content_sha256
    assert downloaded_manifest.content == manifest_bytes
    assert snapshot.cases[0].annotation_revision == evaluation.get_revision(
        document.head_revision_id
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


def _artifact_service(settings: Settings, store: S3ObjectStore) -> ArtifactService:
    return ArtifactService(
        repository=PostgresArtifactRepository(settings.database_url),
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=settings.artifact_max_bytes,
        upload_url_seconds=settings.artifact_upload_url_seconds,
        download_url_seconds=settings.artifact_signed_url_seconds,
    )


def _upload_artifact(service: ArtifactService):
    content = b"\x89PNG\r\n\x1a\n" + uuid4().bytes
    instructions = service.begin_multipart_upload(
        original_filename="prd-010-live.png",
        declared_media_type="image/png",
        expected_byte_size=len(content),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri=f"urn:hiveblot:test:prd-010:{uuid4()}",
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
