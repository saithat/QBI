from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hiveblot_contracts import (
    CaseArtifactRole,
    CaseSourceArtifact,
    FieldAnnotation,
    FieldPathTarget,
    ModelIdentifier,
    ObservationState,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    EvaluationService,
    InvalidEvaluationState,
    StructuredAnnotationService,
)

from tests.fakes.evaluation import InMemoryEvaluationRepository
from tests.fakes.structured import structured_annotation

NOW = datetime(2026, 8, 2, 16, tzinfo=UTC)


def make_service():
    evaluation = EvaluationService(InMemoryEvaluationRepository(), clock=lambda: NOW)
    case = evaluation.create_case(
        case_key=f"structured:{uuid4()}",
        dataset_id=None,
        source_artifacts=(
            CaseSourceArtifact(
                artifact_id=uuid4(),
                role=CaseArtifactRole.FIGURE,
                page_number=1,
            ),
        ),
    )
    return evaluation, StructuredAnnotationService(evaluation), case


def add_prediction(evaluation, case, annotation):
    return evaluation.add_prediction(
        case.case_id,
        prediction_schema="western-blot-structured-annotation",
        prediction_schema_version="1.0",
        producer=ModelIdentifier(provider="fixture", name="extractor", version="1"),
        pipeline=None,
        raw_output_json="historic raw model text",
        normalized_output_json=annotation.model_dump_json(),
        configuration_json="{}",
        evidence=(),
        validation_issues=(),
        confidence=0.91,
        trace_id=uuid4(),
        latency_ms=12,
        cost_microusd=0,
    )


def test_autosave_appends_revisions_and_undo_appends_restored_snapshot() -> None:
    evaluation, service, case = make_service()
    reviewer_id = uuid4()
    original = structured_annotation("TP53")
    document, first = service.save(
        case.case_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=None,
        annotation=original,
        rationale="Accepted prediction",
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
        rationale="Use source spelling",
        error_codes=("incorrect_value",),
    )

    with pytest.raises(ConcurrencyConflict):
        service.save(
            case.case_id,
            reviewer_id=reviewer_id,
            expected_head_revision_id=first.revision_id,
            annotation=original,
            rationale="Stale autosave",
            error_codes=(),
        )

    document, restored = service.undo(
        document.annotation_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=second.revision_id,
        target_revision_id=first.revision_id,
        rationale="Undo source spelling edit",
    )

    assert document.revision_count == 3
    assert restored.prior_revision_id == second.revision_id
    assert restored.structured_annotation == original
    assert evaluation.get_revision(second.revision_id).structured_annotation == corrected


def test_prediction_entities_can_be_accepted_individually_or_in_bulk_and_compared() -> None:
    evaluation, service, case = make_service()
    predicted = structured_annotation("TP53", reviewer_notes=None)
    prediction = add_prediction(evaluation, case, predicted)
    target_id = predicted.proteins[0].entity_id

    individual = service.accept_prediction(
        case.case_id,
        prediction_id=prediction.prediction_id,
        base_annotation=None,
        accepted_entity_ids=(target_id,),
        accept_all=False,
    )
    accepted_all = service.accept_prediction(
        case.case_id,
        prediction_id=prediction.prediction_id,
        base_annotation=individual,
        accepted_entity_ids=(),
        accept_all=True,
    )
    comparison = service.compare(
        prediction_id=prediction.prediction_id,
        prediction=predicted,
        annotation_revision_id=None,
        annotation=accepted_all,
    )

    assert individual.proteins == (predicted.proteins[0],)
    assert not individual.relationships
    assert len(accepted_all.proteins) == 2
    assert accepted_all.relationships == predicted.relationships
    assert comparison.modified_count == 0
    assert comparison.added_count == 0
    assert comparison.removed_count == 0
    assert comparison.unchanged_count > 0


def test_structured_autosave_carries_existing_generic_snapshot_forward() -> None:
    evaluation, service, case = make_service()
    reviewer_id = uuid4()
    field = FieldAnnotation(
        field_annotation_id=uuid4(),
        target=FieldPathTarget(field_path="/legacy/target"),
        state=ObservationState.PRESENT,
        value="p53",
        original_extracted_text="P53",
    )
    document, generic_revision = evaluation.create_annotation(
        case.case_id,
        reviewer_id=reviewer_id,
        rationale="Legacy generic review",
        error_codes=(),
        field_annotations=(field,),
        spatial_annotations=(),
        relationships=(),
    )

    updated, structured_revision = service.save(
        case.case_id,
        reviewer_id=reviewer_id,
        expected_head_revision_id=generic_revision.revision_id,
        annotation=structured_annotation(),
        rationale="Add typed snapshot",
        error_codes=(),
    )

    assert updated.annotation_id == document.annotation_id
    assert structured_revision.field_annotations == (field,)
    assert structured_revision.structured_annotation is not None


def test_canonical_lookup_and_invalid_prediction_are_explicit() -> None:
    evaluation, service, case = make_service()
    candidates = service.lookup_entities(query="p53", entity_type=None, limit=5)
    invalid = evaluation.add_prediction(
        case.case_id,
        prediction_schema="legacy",
        prediction_schema_version="0.1",
        producer=ModelIdentifier(provider="fixture", name="legacy", version="1"),
        pipeline=None,
        raw_output_json="raw",
        normalized_output_json='{"target":"p53"}',
        configuration_json="{}",
        evidence=(),
        validation_issues=(),
        confidence=None,
        trace_id=uuid4(),
        latency_ms=1,
        cost_microusd=0,
    )

    assert candidates[0].canonical_id == "uniprot:P04637"
    with pytest.raises(InvalidEvaluationState, match="not a western-blot structured annotation"):
        service.prediction_annotation(case.case_id, invalid.prediction_id)
