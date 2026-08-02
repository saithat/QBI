from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    AnnotationRelationship,
    BoundingRegion,
    CaseArtifactRole,
    CaseSourceArtifact,
    FieldAnnotation,
    FieldPathTarget,
    ModelIdentifier,
    ObservationState,
    PredictionEvidence,
    ReviewStatus,
    SpatialAnnotation,
    SpatialAnnotationType,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    InvalidEvaluationState,
)

from tests.fakes.evaluation import InMemoryEvaluationRepository

NOW = datetime(2026, 8, 2, 15, tzinfo=UTC)


def make_service():
    repository = InMemoryEvaluationRepository()
    service = EvaluationService(repository, clock=lambda: NOW)
    artifact_id = uuid4()
    case = service.create_case(
        case_key="paper:figure-1:A",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=artifact_id,
                role=CaseArtifactRole.FIGURE,
                page_number=4,
            ),
        ),
    )
    return service, repository, case, artifact_id


def snapshot(artifact_id, *, target: str = "p53"):
    spatial_id = uuid4()
    field_id = uuid4()
    field = FieldAnnotation(
        field_annotation_id=field_id,
        target=FieldPathTarget(field_path="/targets/0/name"),
        state=ObservationState.PRESENT,
        value=target,
        original_extracted_text="P53",
        evidence_region_ids=(spatial_id,),
    )
    spatial = SpatialAnnotation(
        spatial_annotation_id=spatial_id,
        annotation_type=SpatialAnnotationType.BAND,
        state=ObservationState.PRESENT,
        region=BoundingRegion(
            region_id=spatial_id,
            source_artifact_id=artifact_id,
            x=10.0,
            y=20.0,
            width=30.0,
            height=12.0,
            canvas_width=200,
            canvas_height=100,
            page_number=4,
        ),
        label=target,
    )
    relationship = AnnotationRelationship(
        relationship_id=uuid4(),
        subject_id=field_id,
        relation_type="supported_by",
        object_id=spatial_id,
    )
    return (field,), (spatial,), (relationship,)


def test_case_can_hold_multiple_immutable_predictions() -> None:
    service, repository, case, artifact_id = make_service()

    first = service.add_prediction(
        case.case_id,
        prediction_schema="western-blot-extraction",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(
            provider="local",
            name="Qwen3-VL",
            version="baseline",
        ),
        pipeline=None,
        raw_output_json="not valid JSON but preserved exactly",
        normalized_output_json='{"target":"p53"}',
        configuration_json="{}",
        evidence=(),
        validation_issues=(),
        confidence=0.8,
        trace_id=uuid4(),
        latency_ms=120,
        cost_microusd=0,
    )
    second = service.add_prediction(
        case.case_id,
        prediction_schema="western-blot-extraction",
        prediction_schema_version="1.1",
        producer=ModelIdentifier(provider="local", name="Qwen3-VL", version="candidate"),
        pipeline=None,
        raw_output_json='{"target":"TP53"}',
        normalized_output_json='{"target":"TP53"}',
        configuration_json='{"temperature":0}',
        evidence=(),
        validation_issues=(),
        confidence=0.9,
        trace_id=uuid4(),
        latency_ms=100,
        cost_microusd=0,
    )

    assert first.prediction_id != second.prediction_id
    assert service.list_predictions(case.case_id) == (first, second)
    assert repository.predictions[first.prediction_id].raw_output_json.startswith("not valid")
    assert artifact_id == case.source_artifacts[0].artifact_id


def test_reviews_are_independent_and_edits_append_revisions() -> None:
    service, _, case, artifact_id = make_service()
    fields, spatial, relationships = snapshot(artifact_id)
    reviewer_one = uuid4()
    reviewer_two = uuid4()

    document_one, first_revision = service.create_annotation(
        case.case_id,
        reviewer_id=reviewer_one,
        rationale="Initial review",
        error_codes=("incorrect_value",),
        field_annotations=fields,
        spatial_annotations=spatial,
        relationships=relationships,
    )
    document_two, second_review = service.create_annotation(
        case.case_id,
        reviewer_id=reviewer_two,
        rationale="Independent review",
        error_codes=(),
        field_annotations=fields,
        spatial_annotations=spatial,
        relationships=relationships,
    )
    corrected_fields, corrected_spatial, corrected_relationships = snapshot(
        artifact_id,
        target="TP53",
    )
    updated_document, revision_two = service.append_revision(
        document_one.annotation_id,
        expected_head_revision_id=first_revision.revision_id,
        reviewer_id=reviewer_one,
        rationale="Canonicalized target",
        error_codes=(),
        field_annotations=corrected_fields,
        spatial_annotations=corrected_spatial,
        relationships=corrected_relationships,
    )

    assert document_one.annotation_id != document_two.annotation_id
    assert updated_document.revision_count == 2
    assert revision_two.prior_revision_id == first_revision.revision_id
    assert service.get_revision(first_revision.revision_id) == first_revision
    assert service.list_revisions(document_one.annotation_id) == (first_revision, revision_two)
    assert service.get_revision(second_review.revision_id) == second_review

    with pytest.raises(ConcurrencyConflict):
        service.append_revision(
            document_one.annotation_id,
            expected_head_revision_id=first_revision.revision_id,
            reviewer_id=reviewer_one,
            rationale="stale overwrite",
            error_codes=(),
            field_annotations=fields,
            spatial_annotations=spatial,
            relationships=relationships,
        )


def test_adjudication_references_two_reviewers_without_rewriting_them() -> None:
    service, _, case, artifact_id = make_service()
    fields, spatial, relationships = snapshot(artifact_id)
    _, first = service.create_annotation(
        case.case_id,
        reviewer_id=uuid4(),
        rationale=None,
        error_codes=(),
        field_annotations=fields,
        spatial_annotations=spatial,
        relationships=relationships,
    )
    _, second = service.create_annotation(
        case.case_id,
        reviewer_id=uuid4(),
        rationale=None,
        error_codes=(),
        field_annotations=fields,
        spatial_annotations=spatial,
        relationships=relationships,
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
        selected_revision_id=second.revision_id,
        considered_revision_ids=(first.revision_id, second.revision_id),
        rationale="Second review resolves the target spelling.",
    )

    assert adjudication.selected_revision_id == second.revision_id
    assert service.get_revision(first.revision_id) == first
    assert service.get_case(case.case_id).review_status is ReviewStatus.ADJUDICATED


def test_invalid_error_code_and_exclusive_assignment_conflicts_fail_safely() -> None:
    service, _, case, artifact_id = make_service()
    fields, spatial, relationships = snapshot(artifact_id)
    with pytest.raises(InvalidEvaluationState, match="unknown"):
        service.create_annotation(
            case.case_id,
            reviewer_id=uuid4(),
            rationale=None,
            error_codes=("invented_code",),
            field_annotations=fields,
            spatial_annotations=spatial,
            relationships=relationships,
        )

    service.assign_reviewer(case.case_id, reviewer_id=uuid4(), exclusive=True)
    with pytest.raises(ConcurrencyConflict):
        service.assign_reviewer(case.case_id, reviewer_id=uuid4(), exclusive=False)


def test_normalized_prediction_must_be_json_but_raw_output_need_not_be() -> None:
    service, _, case, _ = make_service()
    with pytest.raises(InvalidEvaluationState, match="normalized_output_json"):
        service.add_prediction(
            case.case_id,
            prediction_schema="western-blot-extraction",
            prediction_schema_version="1.0",
            producer=ModelIdentifier(provider="local", name="model", version="1"),
            pipeline=None,
            raw_output_json="raw malformed output is retained",
            normalized_output_json="not-json",
            configuration_json="{}",
            evidence=(),
            validation_issues=(),
            confidence=None,
            trace_id=uuid4(),
            latency_ms=0,
            cost_microusd=0,
        )


def test_predictions_and_geometry_cannot_reference_artifacts_outside_the_case() -> None:
    service, _, case, _ = make_service()
    unrelated_artifact_id = uuid4()

    with pytest.raises(InvalidEvaluationState, match="case source artifacts"):
        service.add_prediction(
            case.case_id,
            prediction_schema="western-blot-extraction",
            prediction_schema_version="1.0",
            producer=ModelIdentifier(provider="local", name="model", version="1"),
            pipeline=None,
            raw_output_json='{"target":"p53"}',
            normalized_output_json='{"target":"p53"}',
            configuration_json="{}",
            evidence=(
                PredictionEvidence(
                    artifact_id=unrelated_artifact_id,
                    field_path="/targets/0/name",
                ),
            ),
            validation_issues=(),
            confidence=0.8,
            trace_id=uuid4(),
            latency_ms=10,
            cost_microusd=0,
        )

    fields, spatial, relationships = snapshot(unrelated_artifact_id)
    with pytest.raises(InvalidEvaluationState, match="case source artifacts"):
        service.create_annotation(
            case.case_id,
            reviewer_id=uuid4(),
            rationale=None,
            error_codes=(),
            field_annotations=fields,
            spatial_annotations=spatial,
            relationships=relationships,
        )
