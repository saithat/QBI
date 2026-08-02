from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from hiveblot_contracts import (
    ArtifactAcquisitionMethod,
    ArtifactRecord,
    ArtifactRelationshipKind,
    ArtifactVisibility,
    CaseArtifactRole,
    CaseSourceArtifact,
    ComponentInvocationStatus,
    DensitometrySuitability,
    FailedComponentResult,
    InvocationFailureKind,
    ReviewerGeometryReference,
    SpatialAnnotationSet,
    SpatialRelationshipType,
)
from hiveblot_densitometry import DensitometryGeometryResolver, DensitometryService
from hiveblot_evaluation import EvaluationService, PipelineRegistryService, SpatialAnnotationService
from hiveblot_storage import (
    ArtifactService,
    CompletedPart,
    InvalidArtifact,
    SourceAdapterRegistry,
    VerifiedArtifactReader,
)

from tests.fakes.artifacts import InMemoryArtifactRepository, InMemoryObjectStore
from tests.fakes.densitometry import (
    BAND_IDS,
    LANE_IDS,
    TARGET_IDS,
    synthetic_blot_png,
    synthetic_densitometry_annotation_set,
    synthetic_densitometry_input,
)
from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.pipeline_registry import InMemoryPipelineRunRepository

NOW = datetime(2026, 8, 2, 20, tzinfo=UTC)
REVIEWER_ID = UUID("70000000-0000-0000-0000-000000000001")


def test_run_replay_and_geometry_revision_preserve_immutable_attempts() -> None:
    (
        service,
        artifacts,
        artifact_repository,
        object_store,
        evaluation,
        spatial,
        case_id,
        _,
    ) = _make_service()
    _, first_revision = spatial.save(
        case_id,
        reviewer_id=REVIEWER_ID,
        expected_head_revision_id=None,
        annotation_set=synthetic_densitometry_annotation_set(artifacts.artifact_id),
        rationale="Reviewed lane and band geometry",
        error_codes=(),
    )
    configuration = synthetic_densitometry_input().configuration

    first = service.run(
        case_id,
        image_artifact_id=artifacts.artifact_id,
        geometry=ReviewerGeometryReference(annotation_revision_id=first_revision.revision_id),
        loading_control_target_id=None,
        configuration=configuration,
    )

    assert first.result.suitability is DensitometrySuitability.QUANTITATIVE
    assert first.result.input.loading_control_target_id == TARGET_IDS[1]
    assert first.result.measurements[0].corrected_intensity == 5600.0
    assert first.result.measurements[0].normalized_intensity == 1.166667
    overlay = artifact_repository.artifacts[first.result.analysis_overlay.artifact_id]
    assert overlay.acquisition_method is ArtifactAcquisitionMethod.TOOL_OUTPUT
    assert overlay.relationships[0].kind is ArtifactRelationshipKind.DERIVED_FROM
    assert overlay.relationships[0].related_artifact_id == artifacts.artifact_id
    overlay_key = artifact_repository.storage_keys[overlay.artifact_id]
    assert object_store.objects[overlay_key].startswith(b"\x89PNG\r\n\x1a\n")

    replay = service.replay(first.invocation_id)

    assert replay.invocation_id != first.invocation_id
    assert replay.publication_id != first.publication_id
    assert replay.result.result_id == first.result.result_id
    assert replay.result.analysis_overlay.artifact_id == first.result.analysis_overlay.artifact_id
    assert len(service.list_attempts(case_id)) == 2

    current = synthetic_densitometry_annotation_set(artifacts.artifact_id)
    shifted_annotations = tuple(
        item.model_copy(update={"region": item.region.model_copy(update={"x": 6.0})})
        if item.spatial_annotation_id == BAND_IDS[0]
        else item
        for item in current.spatial_annotations
    )
    shifted = SpatialAnnotationSet.model_validate(
        {
            "spatial_annotations": shifted_annotations,
            "relationships": current.relationships,
        }
    )
    _, second_revision = spatial.save(
        case_id,
        reviewer_id=REVIEWER_ID,
        expected_head_revision_id=first_revision.revision_id,
        annotation_set=shifted,
        rationale="Shifted the first p53 band by one pixel",
        error_codes=(),
    )
    adjusted = service.run(
        case_id,
        image_artifact_id=artifacts.artifact_id,
        geometry=ReviewerGeometryReference(annotation_revision_id=second_revision.revision_id),
        loading_control_target_id=TARGET_IDS[1],
        configuration=configuration,
    )

    assert adjusted.result.result_id != first.result.result_id
    assert adjusted.result.measurements[0].corrected_intensity == 5040.0
    assert adjusted.result.measurements[0].normalized_intensity == 1.05
    assert len(service.list_attempts(case_id)) == 3
    assert service.get_attempt(first.invocation_id).result == first.result
    assert len(evaluation.list_revisions(first_revision.annotation_id)) == 2


def test_geometry_options_expose_exact_reviewer_revision_and_source_kind() -> None:
    service, artifact, _, _, _, spatial, case_id, _ = _make_service(
        source_role=CaseArtifactRole.FIGURE
    )
    annotation_set = synthetic_densitometry_annotation_set(artifact.artifact_id)
    relationships = tuple(
        relationship.model_copy(
            update={
                "subject_id": relationship.object_id,
                "object_id": relationship.subject_id,
            }
        )
        if relationship.relation_type is SpatialRelationshipType.PRECEDES
        else relationship
        for relationship in annotation_set.relationships
    )
    reversed_lanes = SpatialAnnotationSet.model_validate(
        {
            "spatial_annotations": annotation_set.spatial_annotations,
            "relationships": relationships,
        }
    )
    _, revision = spatial.save(
        case_id,
        reviewer_id=REVIEWER_ID,
        expected_head_revision_id=None,
        annotation_set=reversed_lanes,
        rationale="Publication geometry",
        error_codes=(),
    )

    options = service.geometry_options(case_id)

    assert len(options) == 1
    assert options[0].geometry == ReviewerGeometryReference(
        annotation_revision_id=revision.revision_id
    )
    assert options[0].image_artifact.artifact_id == artifact.artifact_id
    assert options[0].image_kind.value == "publication_figure"
    assert tuple(lane.lane_id for lane in options[0].lanes) == (LANE_IDS[1], LANE_IDS[0])
    assert options[0].inferred_loading_control_target_ids == (TARGET_IDS[1],)


def test_source_integrity_failure_is_recorded_as_a_terminal_attempt() -> None:
    service, artifact, repository, store, _, spatial, case_id, pipeline_repository = _make_service()
    _, revision = spatial.save(
        case_id,
        reviewer_id=REVIEWER_ID,
        expected_head_revision_id=None,
        annotation_set=synthetic_densitometry_annotation_set(artifact.artifact_id),
        rationale="Integrity failure fixture",
        error_codes=(),
    )
    source_key = repository.storage_keys[artifact.artifact_id]
    store.objects[source_key] = b"corrupted after publication"

    with pytest.raises(InvalidArtifact, match="source size changed"):
        service.run(
            case_id,
            image_artifact_id=artifact.artifact_id,
            geometry=ReviewerGeometryReference(annotation_revision_id=revision.revision_id),
            loading_control_target_id=TARGET_IDS[1],
            configuration=synthetic_densitometry_input().configuration,
        )

    invocation = next(iter(pipeline_repository.invocations.values()))
    assert invocation.status is ComponentInvocationStatus.FAILED
    assert isinstance(invocation.result, FailedComponentResult)
    assert invocation.result.failure_kind is InvocationFailureKind.EXECUTION
    assert invocation.result.error_code == "densitometry_execution_failed"
    assert len(repository.artifacts) == 1


def _make_service(
    *,
    source_role: CaseArtifactRole = CaseArtifactRole.RAW_SOURCE,
) -> tuple[
    DensitometryService,
    ArtifactRecord,
    InMemoryArtifactRepository,
    InMemoryObjectStore,
    EvaluationService,
    SpatialAnnotationService,
    UUID,
    InMemoryPipelineRunRepository,
]:
    repository = InMemoryArtifactRepository()
    store = InMemoryObjectStore()
    artifact_service = ArtifactService(
        repository=repository,
        object_store=store,
        source_adapters=SourceAdapterRegistry({}),
        max_bytes=10_000_000,
        upload_url_seconds=3600,
        download_url_seconds=900,
        clock=lambda: NOW,
    )
    image = synthetic_blot_png()
    upload = artifact_service.begin_multipart_upload(
        original_filename="synthetic-blot.png",
        declared_media_type="image/png",
        expected_byte_size=len(image),
        part_count=1,
        visibility=ArtifactVisibility.PUBLIC,
        organization_id=None,
        source_uri="urn:hiveblot:fixture:synthetic-blot",
        relationships=(),
        actor_id=None,
    )
    session = repository.uploads[upload.upload_id]
    assert session.backend_upload_id is not None
    etag = store.put_part(session.backend_upload_id, 1, image)
    published = artifact_service.complete_multipart_upload(
        upload.upload_id,
        (CompletedPart(part_number=1, etag=etag),),
        actor_id=None,
    )
    reader = VerifiedArtifactReader(artifact_service, repository, store)
    evaluation = EvaluationService(InMemoryEvaluationRepository(), clock=lambda: NOW)
    case = evaluation.create_case(
        case_key=f"densitometry:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=published.artifact.artifact_id,
                role=source_role,
            ),
        ),
    )
    spatial = SpatialAnnotationService(evaluation)
    pipeline_repository = InMemoryPipelineRunRepository()
    pipelines = PipelineRegistryService(
        pipeline_repository,
        evaluation,
        artifact_service,
        clock=lambda: NOW,
    )
    geometry = DensitometryGeometryResolver(evaluation, spatial, reader)
    return (
        DensitometryService(reader, artifact_service, pipelines, geometry),
        published.artifact,
        repository,
        store,
        evaluation,
        spatial,
        case.case_id,
        pipeline_repository,
    )
