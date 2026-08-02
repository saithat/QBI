from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    AnnotationRelationship,
    BoundingRegion,
    CaseArtifactRole,
    CaseSourceArtifact,
    ModelIdentifier,
    PredictionEvidence,
    SpatialAnnotationSet,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    InvalidEvaluationState,
    SpatialAnnotationService,
)

from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.spatial import spatial_annotation_set
from tests.fakes.structured import structured_annotation

NOW = datetime(2026, 8, 2, 18, tzinfo=UTC)


def make_service():
    evaluation = EvaluationService(InMemoryEvaluationRepository(), clock=lambda: NOW)
    artifact_id = uuid4()
    case = evaluation.create_case(
        case_key=f"spatial:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact_id,
                role=CaseArtifactRole.FIGURE,
                page_number=1,
            ),
        ),
    )
    return evaluation, SpatialAnnotationService(evaluation), case, artifact_id


def add_prediction(evaluation, case, artifact_id):
    lane_id = uuid4()
    band_id = uuid4()
    evidence = (
        PredictionEvidence(
            artifact_id=artifact_id,
            field_path="/lanes/0",
            region_id=lane_id,
            region=BoundingRegion(
                region_id=lane_id,
                source_artifact_id=artifact_id,
                x=20.0,
                y=20.0,
                width=80.0,
                height=150.0,
                canvas_width=400,
                canvas_height=200,
                page_number=1,
            ),
            description="lane 1",
        ),
        PredictionEvidence(
            artifact_id=artifact_id,
            field_path="/bands/0",
            region_id=band_id,
            region=BoundingRegion(
                region_id=band_id,
                source_artifact_id=artifact_id,
                x=35.0,
                y=55.0,
                width=45.0,
                height=12.0,
                canvas_width=400,
                canvas_height=200,
                page_number=1,
            ),
            description="TP53 band",
        ),
    )
    return evaluation.add_prediction(
        case.case_id,
        prediction_schema="western-blot-spatial",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(provider="fixture", name="detector", version="1"),
        pipeline=None,
        raw_output_json="raw geometry",
        normalized_output_json="{}",
        configuration_json="{}",
        evidence=evidence,
        validation_issues=(),
        confidence=0.88,
        trace_id=uuid4(),
        latency_ms=5,
        cost_microusd=0,
    )


def test_spatial_saves_are_immutable_concurrent_and_undoable() -> None:
    evaluation, service, case, artifact_id = make_service()
    reviewer_id = uuid4()
    original = spatial_annotation_set(artifact_id)
    document, first = service.save(
        case.case_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=None,
        annotation_set=original,
        rationale="Initial geometry",
        error_codes=(),
    )
    assert all(
        relationship.relation_type.startswith("spatial:") for relationship in first.relationships
    )
    first_region = original.spatial_annotations[0]
    adjusted = original.model_copy(
        update={
            "spatial_annotations": (
                first_region.model_copy(
                    update={
                        "region": first_region.region.model_copy(update={"x": 1.0, "width": 399.0})
                    }
                ),
                *original.spatial_annotations[1:],
            )
        }
    )
    document, second = service.save(
        case.case_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=first.revision_id,
        annotation_set=SpatialAnnotationSet.model_validate(adjusted.model_dump(mode="python")),
        rationale="Adjusted figure boundary",
        error_codes=("incorrect_geometry",),
    )
    with pytest.raises(ConcurrencyConflict):
        service.save(
            case.case_id,
            reviewer_id=reviewer_id,
            expected_head_revision_id=first.revision_id,
            annotation_set=original,
            rationale="Stale geometry",
            error_codes=(),
        )

    _, restored = service.undo(
        document.annotation_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=second.revision_id,
        target_revision_id=first.revision_id,
        rationale="Restore geometry",
    )

    assert restored.prior_revision_id == second.revision_id
    assert restored.spatial_annotations == original.spatial_annotations
    assert len(evaluation.list_revisions(document.annotation_id)) == 3


def test_prediction_regions_accept_individually_and_diff_semantically() -> None:
    evaluation, service, case, artifact_id = make_service()
    prediction = add_prediction(evaluation, case, artifact_id)
    _, predicted = service.prediction_set(case.case_id, prediction.prediction_id)
    lane = predicted.spatial_annotations[0]
    individual = service.accept_prediction(
        case.case_id,
        prediction_id=prediction.prediction_id,
        base=None,
        accepted_region_ids=(lane.spatial_annotation_id,),
        accept_all=False,
    )
    moved_lane = lane.model_copy(
        update={"region": lane.region.model_copy(update={"x": lane.region.x + 2.0})}
    )
    reviewed = SpatialAnnotationSet(spatial_annotations=(moved_lane,))
    comparison = service.compare(
        prediction_id=prediction.prediction_id,
        prediction=predicted,
        annotation_revision_id=None,
        annotation=reviewed,
    )

    assert individual.spatial_annotations == (lane,)
    assert comparison.modified_count == 1
    assert comparison.removed_count == 1
    lane_delta = next(
        delta
        for delta in comparison.deltas
        if delta.spatial_annotation_id == lane.spatial_annotation_id
    )
    assert lane_delta.changed_fields == ("region.x",)


def test_prediction_acceptance_rejects_a_geometry_that_breaks_current_graph() -> None:
    evaluation, service, case, artifact_id = make_service()
    current = spatial_annotation_set(artifact_id)
    band = current.spatial_annotations[7]
    outside_lane = band.region.model_copy(update={"x": 350.0})
    prediction = evaluation.add_prediction(
        case.case_id,
        prediction_schema="western-blot-spatial",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(provider="fixture", name="detector", version="invalid-merge"),
        pipeline=None,
        raw_output_json="raw geometry",
        normalized_output_json="{}",
        configuration_json="{}",
        evidence=(
            PredictionEvidence(
                artifact_id=artifact_id,
                field_path="/bands/0",
                region_id=outside_lane.region_id,
                region=outside_lane,
                description="band",
            ),
        ),
        validation_issues=(),
        confidence=0.5,
        trace_id=uuid4(),
        latency_ms=1,
        cost_microusd=0,
    )

    with pytest.raises(InvalidEvaluationState, match="conflicts with current"):
        service.accept_prediction(
            case.case_id,
            prediction_id=prediction.prediction_id,
            base=current,
            accepted_region_ids=(band.spatial_annotation_id,),
            accept_all=False,
        )


def test_spatial_save_preserves_structured_snapshot_and_unknown_relationships() -> None:
    evaluation, service, case, artifact_id = make_service()
    reviewer_id = uuid4()
    structured = structured_annotation()
    unknown_relationship = AnnotationRelationship(
        relationship_id=uuid4(),
        subject_id=uuid4(),
        relation_type="contains",
        object_id=uuid4(),
    )
    document, first = evaluation.create_annotation(
        case.case_id,
        reviewer_id=reviewer_id,
        rationale="Structured review",
        error_codes=(),
        field_annotations=(),
        spatial_annotations=(),
        relationships=(unknown_relationship,),
        structured_annotation=structured,
    )
    _, second = service.save(
        case.case_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=first.revision_id,
        annotation_set=spatial_annotation_set(artifact_id),
        rationale="Add spatial graph",
        error_codes=(),
    )

    assert document.annotation_id == second.annotation_id
    assert second.structured_annotation == structured
    assert unknown_relationship in second.relationships


def test_spatial_save_rejects_relationship_id_collision_with_preserved_data() -> None:
    evaluation, service, case, artifact_id = make_service()
    reviewer_id = uuid4()
    spatial = spatial_annotation_set(artifact_id)
    collision = AnnotationRelationship(
        relationship_id=spatial.relationships[0].relationship_id,
        subject_id=uuid4(),
        relation_type="supported_by",
        object_id=uuid4(),
    )
    _, first = evaluation.create_annotation(
        case.case_id,
        reviewer_id=reviewer_id,
        rationale="Existing review",
        error_codes=(),
        field_annotations=(),
        spatial_annotations=(),
        relationships=(collision,),
    )

    with pytest.raises(InvalidEvaluationState, match="must not collide"):
        service.save(
            case.case_id,
            reviewer_id=reviewer_id,
            expected_head_revision_id=first.revision_id,
            annotation_set=spatial,
            rationale="Conflicting relationship identifier",
            error_codes=(),
        )


def test_spatial_undo_preserves_current_non_spatial_review_data() -> None:
    evaluation, service, case, artifact_id = make_service()
    reviewer_id = uuid4()
    original = spatial_annotation_set(artifact_id)
    document, first = service.save(
        case.case_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=None,
        annotation_set=original,
        rationale="Initial geometry",
        error_codes=(),
    )
    band = original.spatial_annotations[7]
    adjusted_annotations = (
        *original.spatial_annotations[:7],
        band.model_copy(update={"region": band.region.model_copy(update={"x": band.region.x + 2})}),
        *original.spatial_annotations[8:],
    )
    current_relationship = AnnotationRelationship(
        relationship_id=uuid4(),
        subject_id=uuid4(),
        relation_type="supported_by",
        object_id=uuid4(),
    )
    corrected_structured = structured_annotation("p53")
    _, second = evaluation.append_revision(
        document.annotation_id,
        expected_head_revision_id=first.revision_id,
        reviewer_id=reviewer_id,
        rationale="Structured and geometry edit",
        error_codes=("incorrect_value",),
        field_annotations=first.field_annotations,
        spatial_annotations=adjusted_annotations,
        relationships=(*first.relationships, current_relationship),
        structured_annotation=corrected_structured,
    )

    _, restored = service.undo(
        document.annotation_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=second.revision_id,
        target_revision_id=first.revision_id,
        rationale="Restore only geometry",
    )

    assert restored.spatial_annotations == original.spatial_annotations
    assert restored.structured_annotation == corrected_structured
    assert restored.error_codes == second.error_codes
    assert current_relationship in restored.relationships
